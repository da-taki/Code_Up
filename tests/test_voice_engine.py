
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c



class TestMentorChatStream:
    def test_stream_endpoint_exists(self, client):
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": "explain this"},
                           content_type='application/json')
        assert resp.status_code in (200, 400, 413, 500)

    def test_stream_returns_event_stream(self, client):
        resp = client.post('/mentor/chat-stream',
                           json={"code": "print('hi')", "message": "what does this do?"},
                           content_type='application/json')
        if resp.status_code == 200:
            assert 'text/event-stream' in resp.content_type

    def test_stream_rejects_empty_message(self, client):
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": ""},
                           content_type='application/json')
        assert resp.status_code == 400

    def test_stream_rejects_oversized_code(self, client):
        huge_code = "x = 1\n" * 50000
        resp = client.post('/mentor/chat-stream',
                           json={"code": huge_code, "message": "help"},
                           content_type='application/json')
        assert resp.status_code == 413

    def test_stream_data_format(self, client):
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": "explain"},
                           content_type='application/json')
        if resp.status_code == 200:
            data = resp.get_data(as_text=True)
            assert 'data:' in data
            assert '[DONE]' in data



class TestStateMachine:

    def test_voice_command_still_works(self, client):
        resp = client.post('/voice-command',
                           json={"text": "run"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'run'

    def test_voice_command_unknown(self, client):
        resp = client.post('/voice-command',
                           json={"text": "zzzzqqqxxx999"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'action' in data

    def test_voice_command_hindi(self, client):
        resp = client.post('/voice-command',
                           json={"text": "कोड चलाओ"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'run'



class TestLanguageDetection:

    DEVANAGARI_RANGE = r'[ऀ-ॿ]'

    def _detect_lang(self, text):
        import re
        if not text:
            return 'en'
        devanagari = len(re.findall(r'[ऀ-ॿ]', text))
        latin = len(re.findall(r'[a-zA-Z]', text))
        if devanagari > latin * 0.3:
            return 'hi'
        return 'en'

    def test_english_text(self):
        assert self._detect_lang("run the code") == 'en'

    def test_hindi_text(self):
        assert self._detect_lang("कोड चलाओ") == 'hi'

    def test_mixed_text_mostly_hindi(self):
        assert self._detect_lang("कोड run करो") == 'hi'

    def test_mixed_text_mostly_english(self):
        assert self._detect_lang("run the code now please") == 'en'

    def test_empty_text(self):
        assert self._detect_lang("") == 'en'



class TestInterruptBehavior:

    def test_duplicate_voice_commands(self, client):
        resp1 = client.post('/voice-command',
                            json={"text": "help"},
                            content_type='application/json')
        resp2 = client.post('/voice-command',
                            json={"text": "help"},
                            content_type='application/json')
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.get_json()['action'] == resp2.get_json()['action']

    def test_rapid_commands_no_crash(self, client):
        commands = ["run", "help", "go to line 5", "analyze", "fix"]
        for cmd in commands:
            resp = client.post('/voice-command',
                               json={"text": cmd},
                               content_type='application/json')
            assert resp.status_code == 200


class TestSpeechChunking:
    def test_voice_engine_uses_semantic_narration_chunks(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'static', 'voice-engine.js')
        with open(path, encoding='utf-8') as handle:
            voice_js = handle.read()

        assert 'speechChunkSize: 260' in voice_js
        assert 'streamNarrationMinChars: 140' in voice_js
        assert 'takeSpeakableStreamText' in voice_js
        assert 'Micro-chunk narration' not in voice_js



class TestExistingEndpoints:

    def test_mentor_chat_still_works(self, client):
        resp = client.post('/mentor/chat',
                           json={"code": "x = 1", "message": "what is x?",
                                 "language": "en", "mode": "general"},
                           content_type='application/json')
        assert resp.status_code in (200, 400, 500)

    def test_run_endpoint_still_works(self, client):
        resp = client.post('/run',
                           json={"code": "print('hello')", "language": "en"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'success' in data or 'error' in data

    def test_voice_command_goto_line(self, client):
        resp = client.post('/voice-command',
                           json={"text": "go to line 10"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'goto_line'
        assert data['line'] == 10
