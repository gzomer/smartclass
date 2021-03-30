import os
import time
import json
import requests
import re
import hashlib
import time
import datetime

from nltk import pos_tag, word_tokenize
import youtube_dl

from pyyoutube import Api as YoutubeAPI
from os import path
from random import shuffle
from collections import defaultdict
from functools import partial
from urllib.parse import urljoin, urlparse

from bson.objectid import ObjectId
from flask import Flask, flash, render_template, request as req, redirect, session
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask import jsonify
from slugify import slugify
from symbl import Symbl
from rake_nltk import Rake
from nltk.corpus import wordnet as wn

youtube_api = YoutubeAPI(api_key=os.environ["YOUTUBE_API_KEY"])

MESSAGE_CONTEXT_BEFORE = 6
MESSAGE_CONTEXT_AFTER = 6
EXTRACT_KEYWORDS_COUNT = 15

class CustomFlask(Flask):
    jinja_options = Flask.jinja_options.copy()
    jinja_options.update(dict(
        block_start_string='<%',
        block_end_string='%>',
        variable_start_string='[[',
        variable_end_string=']]',
        comment_start_string='<#',
        comment_end_string='#>',
    ))

app = CustomFlask(__name__)
app.config["MONGO_URI"] = "mongodb://localhost:27017/smartclass"
app.config['SECRET_KEY'] = 'SMARTCLASS_SECRETKEY'
mongo = PyMongo(app)
CORS(app)

app.jinja_env.globals['app_name'] = 'SMART CLASS'
app.jinja_env.globals['app_title'] = 'Smart Class - Learn faster with conversation intelligence'
app.jinja_env.globals['app_description'] = 'Smart Class allows students to easily review lectures by transcribing the recordings, organizing the content, and adding insights in a user-friendly interface.'
app.jinja_env.globals['current_year'] = datetime.datetime.now().year

BASE_URL = 'https://smartclass.futur.technology'

@app.before_request
def handle_user_auth():
    should_create = True

    if session.get('user_id'):
        user = mongo.db.User.find_one({'_id':ObjectId(session.get('user_id'))})
        if user:
            should_create = False

    if should_create:
        user = mongo.db.User.insert_one({'contents':[]})
        session['user_id'] = str(user.inserted_id)

@app.route('/add', methods=["GET", "POST"])
def add_content():

    def is_url_valid(url):
        regex = re.compile(
        r'^(?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
        r'localhost|' #localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)

        return re.match(regex, url) is not None

    url = req.args.get('url')
    if not url or not is_url_valid(url):
        flash('Invalid url.')
        return redirect('/')

    youtube_id = get_youtube_id(url)

    if not youtube_id:
        flash('Invalid Youtube video.')
        return redirect('/')

    content = mongo.db.Content.find_one({'youtubeId': youtube_id})

    if content:
        return redirect(f'/content/{content["slug"]}/{content["_id"]}')
    else:
        video = youtube_api.get_video_by_id(video_id=youtube_id)

        if len(video.items) == 0:
            flash('Invalid Youtube video.')
            return redirect('/')

        if video.items[0].contentDetails.get_video_seconds_duration() > 10*60:
            flash('Please upgrade your account to use recordings longer than 10 minutes or use a shorter lecture.')
            return redirect('/')

        audio_url = get_content_audio_url(url)

        if not audio_url:
            flash('Couldn\'t process Youtube video')
            return redirect('/')

        symbl_api = Symbl()
        response = symbl_api.convert_audio(audio_url, diarization=3)

        if 'message' in response:
            flash(response['message'])
            return redirect('/')

        title = video.items[0].snippet.title
        description = video.items[0].snippet.description
        slug = slugify(title)

        data = {
            'slug' : slug,
            'title': title,
            'url': url,
            'youtubeId': youtube_id,
            'description': description,
            'conversationId': response['conversationId'],
            'jobId': response['jobId'],
            'jobStatus': 'in_progress'
        }

        content = mongo.db.Content.insert_one(data)

        if session.get('user_id'):
            mongo.db.User.update_one(
                {'_id': ObjectId(session.get('user_id'))},
                {'$addToSet': {
                    'contents': content.inserted_id
                }}
            )

        return redirect(f'/content/{slug}/{content.inserted_id}')


@app.route('/content/<title>/<id>')
def content(title, id):

    content = mongo.db.Content.find_one({'_id': ObjectId(id)})

    # Check if conversation has been processed
    if content.get('jobStatus', None) != 'completed':
        symbl_api = Symbl()
        response = symbl_api.job_status(content['jobId'])
        if response['status'] == 'completed':
            mongo.db.Content.update_one(
                {'_id': ObjectId(content['_id'])},
                {'$set': {
                    'jobStatus': 'completed'
                }})
        else:
            return render_template('content.html', content=content, conversation={})

    conversation_id = content['conversationId']

    # Start Symbl API and get token
    symbl_api = Symbl()
    conversation = symbl_api.conversation(conversation_id)

    def convert_timestamp(time_string):
        return datetime.datetime.strptime(time_string, "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()

    start_time = convert_timestamp(conversation['startTime'])

    messages = symbl_api.messages(conversation_id)
    topics = symbl_api.topics(conversation_id)
    follow_ups = symbl_api.follow_ups(conversation_id)
    action_items = symbl_api.action_items(conversation_id)

    questions = symbl_api.questions(conversation_id)

    def convert_message_timestamp(message):
        message['startTime'] = round((convert_timestamp(message['startTime']) - start_time), 2)
        del message['phrases']
        return message

    def get_keywords(messages):
        rake = Rake()
        rake.extract_keywords_from_sentences([message['text'] for message in messages])

        # Only bi-grams
        filtered = [item for item in rake.get_ranked_phrases_with_scores() if len(item[1].split()) == 2]

        # Filter only nouns bi-grams
        keywords_with_score = []
        for item in filtered:
            score = item[0]
            keyword = item[1]
            words = keyword.split()
            should_include = True
            tags = pos_tag(words)
            should_include = 'NN' in tags[0][1] and 'NN' == tags[1][1]
            for word in words:
                synset = wn.synsets(word)
                if not synset:
                    should_include = False
                    break
                if synset[0].pos() != 'n':
                    should_include = False

            if should_include:
                keywords_with_score.append(item)

        return [item[1] for item in keywords_with_score[:EXTRACT_KEYWORDS_COUNT]]

    def replace_keyword_link(text, keyword):
        return text.replace(keyword, f'<a target="_blank" href="https://en.wikipedia.org/wiki/{keyword}">{keyword}</a>')

    def enrich_messages_with_keywords(messages):
        keywords = list(set(get_keywords(messages)))
        for message in messages:
            for keyword in keywords:
                if keyword in message['text']:
                    message['text'] = replace_keyword_link(message['text'], keyword)

        return messages

    messages = list(map(convert_message_timestamp, messages['messages']))
    messages = enrich_messages_with_keywords(messages)

    messages_map = {message['id']:message for message in messages}
    messages_ids = [item['id'] for item in messages]
    messages_ids_to_index = {item['id']:index for index, item in enumerate(messages)}

    def map_message_type(data, message_type):
        for item in data:
            for message_id in item['messageIds']:
                messages_map[message_id].update({'type': message_type})

    map_message_type(questions['questions'], 'question')
    map_message_type(action_items['actionItems'], 'action')
    map_message_type(follow_ups['followUps'], 'followups')

    unique_topics = []
    unique_topics_map = {}

    # Remove duplicated topics
    for topic in topics['topics']:
        if topic['text'] not in unique_topics_map:
            unique_topics_map[topic['text']] = True
            unique_topics.append(topic)

    def resolve_message_references(data):
        for item in data:
            if not 'messageIds' in item:
                item['messages'] = []
            else:
                item['messages'] = [messages_map[message_id] for message_id in item['messageIds']]
        return data

    def add_message_context(data):
        for item in data:
            ids = item['messageIds']
            first_id = ids[0]
            last_id = ids[-1]

            first_index = messages_ids_to_index[first_id]
            last_index = messages_ids_to_index[last_id]

            ids = messages_ids[first_index-MESSAGE_CONTEXT_BEFORE:first_index] + ids
            ids = ids + messages_ids[last_index+1:last_index+MESSAGE_CONTEXT_AFTER+1]

            item['groupFirstMessage'] = messages_map[first_id]
            item['messageIds'] = ids

        return resolve_message_references(data)

    def split_by_speakers(data):
        grouped_speakers = defaultdict(list)
        for item in data:
            if 'messages' in item:
                first_message = item['messages'][0]
            else:
                first_message = item
            grouped_speakers['All speakers'].append(item)
            if 'name' in first_message['from']:
                grouped_speakers[first_message['from']['name']].append(item)

        grouped_speakers = {k : grouped_speakers[k] for k in sorted(grouped_speakers)}
        return [{'key': slugify(name), 'name': name, 'data': group_data} for name, group_data in grouped_speakers.items()]

    conversation_data = {
        'topics' : [],
        'messages': split_by_speakers(messages),
        'questions': split_by_speakers(add_message_context(questions['questions'])),
        'topics': resolve_message_references(unique_topics),
        'action_items': split_by_speakers(add_message_context(action_items['actionItems'])),
        'follow_ups': split_by_speakers(add_message_context(follow_ups['followUps'])),
    }

    return render_template('content.html', content=content, conversation=conversation_data)

@app.route('/explore/')
@app.route('/explore/<search>')
def explore(search=None):
    contents = get_contents(search)
    return render_template('contents.html', explore_url='explore', title='Explore', contents=contents)

@app.route('/contents/')
@app.route('/contents/<search>')
def contents(search=None):
    ids = []
    contents = []

    if session.get('user_id'):
        user = mongo.db.User.find_one({'_id':ObjectId(session.get('user_id'))})
        if user:
            ids = user['contents']

    if ids:
        contents = get_contents(search, ids)

    return render_template('contents.html', explore_url='contents', title='My Materials', contents=contents)

@app.route('/')
def home():
    contents = get_contents()
    return render_template('home.html', hide_search=True, contents=contents)

def get_youtube_id(url):
    regex = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?(?P<id>[A-Za-z0-9\-=_]{11})')
    match = regex.match(url)

    if match:
        return match.group('id')

def get_content_audio_url(url):
    youtube_id = get_youtube_id(url)

    if not youtube_id:
        return

    file_name = f'{youtube_id}'
    full_filename = None

    ydl_opts = {
        'outtmpl': f'{os.getcwd()}/static/media/%(id)s.%(ext)s',
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    base_path = f'./static/media/{file_name}'
    for extension in ['m4a', 'mp3']:
        if os.path.exists(f'{base_path}.{extension}'):
            full_filename = f'{file_name}.{extension}'
            break

    if not full_filename:
        return None

    return f'{BASE_URL}/static/media/{full_filename}'

def get_contents(search=None, ids=None, limit=50):
    query = {}
    if ids:
        query['_id'] = {'$in': ids}

    if search:
        if isinstance(search, list):
            query['tags.all'] = {'$in': search}
        else:
            query['tags.all'] = search

    contents = mongo.db.Content.find(query, {'_id':1, 'title':1, 'slug':1, 'description':1, 'youtubeId': 1}).sort('created_at', -1).limit(limit)
    contents = reversed(list(contents))

    return contents

if __name__ == "__main__":
    app.run(host='0.0.0.0',port=5051, use_reloader=True, debug=True)