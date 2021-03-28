import os
import time
import json
import requests
import re
import hashlib
import time
import datetime

from os import path
from random import shuffle
from collections import defaultdict
from functools import partial
from urllib.parse import urljoin, urlparse

from bson.objectid import ObjectId
from flask import Flask, render_template, request as req, redirect, session
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask import jsonify
from slugify import slugify
import youtube_dl
from symbl import Symbl

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
app.jinja_env.globals['app_title'] = 'Smart Class - Learn faster'
app.jinja_env.globals['app_description'] = 'Smart Class helps you learn faster.'
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
        return redirect('/')

    content = mongo.db.Content.find_one({'url': url})

    if content:
        return redirect(f'/content/{content["slug"]}/{content["_id"]}')
    else:
        audio_url = get_content_audio_url(url)

        if not audio_url:
            return redirect('/?error=Invalid URL')

        symbl_api = Symbl()
        response = symbl_api.convert_audio(audio_url, diarization=3)

        if 'message' in response:
            return redirect('/?error=' + response['message'])

        title = ''
        slug = 'lecture'

        data = {
            'slug' : slug,
            'title': title,
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
    entities = symbl_api.entities(conversation_id)
    questions = symbl_api.questions(conversation_id)

    def convert_message_timestamp(message):
        message['startTime'] = round((convert_timestamp(message['startTime']) - start_time), 2)
        del message['phrases']
        return message

    messages = list(map(convert_message_timestamp, messages['messages']))
    messages_map = {message['id']:message for message in messages}

    def resolve_message_references(data):
        for item in data:
            if not 'messageIds' in item:
                item['messages'] = []
            else:
                item['messages'] = [messages_map[message_id] for message_id in item['messageIds']]
        return data

    conversation_data = {
        'topics' : [],
        'messages': messages,
        'questions': resolve_message_references(questions['questions']),
        'topics': resolve_message_references(topics['topics']),
        'entities': resolve_message_references(entities['entities'])
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

def get_content_audio_url(url):
    regex = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?(?P<id>[A-Za-z0-9\-=_]{11})')
    match = regex.match(url)

    if not match:
        return None

    youtube_id = match.group('id')
    file_name = f'audio-{youtube_id}'
    full_filename = None

    def callback_media(d):
        base_path = f'./static/media/{file_name}'
        if d['status'] == 'finished':
            file_exists = False
            while not file_exists:
                for extension in ['m4a', 'mp3']:
                    full_filename = f'{file_name}.{extension}'
                    if os.path.exists(f'{base_path}.{extension}'):
                        file_exists = True
                        break
                time.sleep(2)

    ydl_opts = {
        'outtmpl': f'{os.path.dirname(__file__)}/static/media/{youtube_id}',
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],

        'progress_hooks': [callback_media],
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

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

    contents = mongo.db.Content.find(query, {'_id':1, 'title':1, 'slug':1, 'description':1}).sort('created_at', -1).limit(limit)
    contents = list(contents)

    return contents

if __name__ == "__main__":
    app.run(host='0.0.0.0',port=5051, use_reloader=True, debug=True)