import requests
import json
import os
import hashlib
import datetime

APP_ID = os.environ.get('APP_ID')
APP_SECRET = os.environ.get('APP_SECRET')

class Symbl():
    expires_in = None
    access_token = None

    def get_token(self):
        if Symbl.expires_in and int(datetime.datetime.now().timestamp()) < Symbl.expires_in:
            return Symbl.access_token

        url = "https://api.symbl.ai/oauth2/token:generate"

        appId = APP_ID
        appSecret = APP_SECRET

        payload = {
            "type": "application",
            "appId": appId,
            "appSecret": appSecret
        }
        headers = {
            'Content-Type': 'application/json'
        }

        response = requests.request("POST", url, headers=headers, data=json.dumps(payload))

        if response.status_code == 200:
            Symbl.expires_in = int(datetime.datetime.now().timestamp() + response.json()['expiresIn']/2)
            Symbl.access_token = response.json()['accessToken']
            return Symbl.access_token
        else:
            raise Exception(response.text)

    def _cache_key(self, url):
        return hashlib.md5(url.encode('utf-8')).hexdigest()

    def _cache_response(self, url, data):
        cache_key = self._cache_key(url)
        with open(f'./cache/{cache_key}.json', 'w') as f:
            f.write(json.dumps(data, indent=4))

    def _get_cached_response(self, url):
        data = None
        cache_key = self._cache_key(url)
        if os.path.exists(f'./cache/{cache_key}.json'):
            with open(f'./cache/{cache_key}.json', 'r') as f:
                data = json.loads(f.read())
        return data

    def _conversation_api(self, conversation_id, method=''):
        url = f"https://api.symbl.ai/v1/conversations/{conversation_id}/{method}"

        cached_response = self._get_cached_response(url)
        if cached_response:
            return cached_response

        headers = {
            'Authorization': 'Bearer ' + self.get_token(),
            'Content-Type': 'application/json'
        }

        response = requests.request("GET", url, headers=headers)
        json_data = response.json()
        self._cache_response(url, json_data)
        return json_data

    def conversation(self, conversation_id):
        return self._conversation_api(conversation_id)

    def entities(self, conversation_id):
        return self._conversation_api(conversation_id, 'entities')

    def messages(self, conversation_id):
        return self._conversation_api(conversation_id, 'messages')

    def action_items(self, conversation_id):
        return self._conversation_api(conversation_id, 'action-items')

    def follow_ups(self, conversation_id):
        return self._conversation_api(conversation_id, 'follow-ups')

    def questions(self, conversation_id):
        return self._conversation_api(conversation_id, 'questions')

    def topics(self, conversation_id):
        url = f"https://api.symbl.ai/v1/conversations/{conversation_id}/topics"

        cached_response = self._get_cached_response(url)
        if cached_response:
            return cached_response

        headers = {
            'Authorization': 'Bearer ' + self.get_token(),
            'Content-Type': 'application/json'
        }

        params = {
            'parentRefs': True,
        }

        response = requests.request("GET", url, headers=headers, params=json.dumps(params))
        json_data = response.json()
        self._cache_response(url, json_data)
        return json_data

    def convert_audio(self, audio_url, diarization=None, detect_phrases=False):

        if diarization:
            url = f"https://api.symbl.ai/v1/process/audio/url?enableSpeakerDiarization=true&diarizationSpeakerCount={diarization}"
        else:
            url = "https://api.symbl.ai/v1/process/audio/url"

        headers = {
            'Authorization': 'Bearer ' + self.get_token(),
            'Content-Type': 'application/json'
        }

        payload = {
            'url': audio_url,
            'detectPhrases': detect_phrases,
            'languageCode': "en-US"
        }
        response = requests.request("POST", url, headers=headers, data=json.dumps(payload))

        return response.json()

    def job_status(self, job_id):
        url = f"https://api.symbl.ai/v1/job/{job_id}"

        headers = {
            'Authorization': 'Bearer ' + self.get_token(),
            'Content-Type': 'application/json'
        }

        response = requests.request("GET", url, headers=headers)
        return response.json()
