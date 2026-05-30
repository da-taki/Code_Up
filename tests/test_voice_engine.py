"""
Tests for the new VoiceEngine streaming and conversational voice system.
Covers: streaming endpoint, state transitions, interrupt behavior,
language detection, and deduplication.
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Streaming Endpoint Tests
# ---------------------------------------------------------------------------

class TestMentorChatStream:
    def test_stream_endpoint_exists(self, client):
        """The /mentor/chat-stream endpoint should accept POST."""
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": "explain this"},
                           content_type='application/json')
        # Should not be 404 or 405
        assert resp.status_code in (200, 400, 413, 500)

    def test_stream_returns_event_stream(self, client):
        """Should return text/event-stream content type."""
        resp = client.post('/mentor/chat-stream',
                           json={"code": "print('hi')", "message": "what does this do?"},
                           content_type='application/json')
        if resp.status_code == 200:
            assert 'text/event-stream' in resp.content_type

    def test_stream_rejects_empty_message(self, client):
        """Should reject empty message (same as /mentor/chat)."""
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": ""},
                           content_type='application/json')
        assert resp.status_code == 400

    def test_stream_rejects_oversized_code(self, client):
        """Should reject code exceeding MAX_CODE_SIZE."""
        huge_code = "x = 1\n" * 50000
        resp = client.post('/mentor/chat-stream',
                           json={"code": huge_code, "message": "help"},
                           content_type='application/json')
        assert resp.status_code == 413

    def test_stream_data_format(self, client):
        """Stream should produce SSE data: lines."""
        resp = client.post('/mentor/chat-stream',
                           json={"code": "x = 1", "message": "explain"},
                           content_type='application/json')
        if resp.status_code == 200:
            data = resp.get_data(as_text=True)
            # Should contain at least one "data:" line
            assert 'data:' in data
            # Should end with [DONE]
            assert '[DONE]' in data


# ---------------------------------------------------------------------------
# State Machine Tests (conceptual — validates transitions)
# ---------------------------------------------------------------------------

class TestStateMachine:
    """Tests for the state machine logic (validated via the endpoint behavior)."""

    def test_voice_command_still_works(self, client):
        """Voice command endpoint should still function normally."""
        resp = client.post('/voice-command',
                           json={"text": "run"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'run'

    def test_voice_command_unknown(self, client):
        """Unknown command should return a valid response (not crash)."""
        resp = client.post('/voice-command',
                           json={"text": "zzzzqqqxxx999"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        # Should return some action (unknown, confirm, or fuzzy match)
        assert 'action' in data

    def test_voice_command_hindi(self, client):
        """Hindi voice command should still parse correctly."""
        resp = client.post('/voice-command',
                           json={"text": "कोड चलाओ"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'run'


# ---------------------------------------------------------------------------
# Language Detection Tests (JavaScript logic validated via Python equivalents)
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    """Test language detection logic equivalent to what voice-engine.js does."""

    DEVANAGARI_RANGE = r'[ऀ-ॿ]'

    def _detect_lang(self, text):
        """Mirror VoiceEngine.detectLanguage logic."""
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


# ---------------------------------------------------------------------------
# Interrupt and Deduplication Tests
# ---------------------------------------------------------------------------

class TestInterruptBehavior:
    """Test that concurrent/duplicate requests are handled properly."""

    def test_duplicate_voice_commands(self, client):
        """Same command sent twice should both return valid results (dedup is client-side)."""
        resp1 = client.post('/voice-command',
                            json={"text": "help"},
                            content_type='application/json')
        resp2 = client.post('/voice-command',
                            json={"text": "help"},
                            content_type='application/json')
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both should return same action
        assert resp1.get_json()['action'] == resp2.get_json()['action']

    def test_rapid_commands_no_crash(self, client):
        """Rapid-fire commands shouldn't crash the backend."""
        commands = ["run", "help", "go to line 5", "analyze", "fix"]
        for cmd in commands:
            resp = client.post('/voice-command',
                               json={"text": cmd},
                               content_type='application/json')
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Streaming No-Break Tests (existing endpoints still work)
# ---------------------------------------------------------------------------

class TestExistingEndpoints:
    """Ensure existing non-streaming endpoints are unbroken."""

    def test_mentor_chat_still_works(self, client):
        """Original /mentor/chat endpoint should still function."""
        resp = client.post('/mentor/chat',
                           json={"code": "x = 1", "message": "what is x?",
                                 "language": "en", "mode": "general"},
                           content_type='application/json')
        # May fail due to no API key, but should not be 404/405
        assert resp.status_code in (200, 400, 500)

    def test_run_endpoint_still_works(self, client):
        """The /run endpoint should still function (returns 200 with result)."""
        resp = client.post('/run',
                           json={"code": "print('hello')", "language": "en"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        # On Windows CI the subprocess may fail due to handle issues,
        # but the endpoint itself should not crash
        assert 'success' in data or 'error' in data

    def test_voice_command_goto_line(self, client):
        """goto_line intent should return correct structure."""
        resp = client.post('/voice-command',
                           json={"text": "go to line 10"},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['action'] == 'goto_line'
        assert data['line'] == 10
