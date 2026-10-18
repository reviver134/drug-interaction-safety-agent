"""Provider diagnostics must distinguish failures without exposing secrets."""
import unittest
from unittest.mock import patch
from google.genai import errors
from pydantic import BaseModel
import gemini_service as gemini


class Result(BaseModel):
    text: str


class GeminiErrorTests(unittest.TestCase):
    def test_provider_errors_are_actionable_and_do_not_expose_payload(self):
        cases = [(400, 'API_KEY_INVALID', 'API key'), (401, 'UNAUTHENTICATED', 'API key'),
                 (403, 'PERMISSION_DENIED', 'restrictions'), (404, 'NOT_FOUND', 'model'),
                 (429, 'RESOURCE_EXHAUSTED', 'quota'), (503, 'UNAVAILABLE', 'temporarily')]
        for code, status, expected in cases:
            with self.subTest(code=code):
                error = errors.APIError(code, {'error': {'code': code, 'status': status,
                    'message': 'PRIVATE-PROVIDER-PAYLOAD ' + status}})
                message = gemini.provider_error_message(error)
                self.assertIn(expected, message)
                self.assertNotIn('PRIVATE-PROVIDER-PAYLOAD', message)

    def test_generate_propagates_sanitized_provider_failure(self):
        error = errors.APIError(401, {'error': {'code':401,'message':'secret-provider-payload'}})
        with patch.dict('os.environ', {'GEMINI_API_KEY':'test-only-key'}), patch('google.genai.Client') as client:
            client.return_value.__enter__.return_value.models.generate_content.side_effect = error
            with self.assertRaises(gemini.GeminiUnavailable) as caught:
                gemini.generate('test', Result, 'test')
        self.assertIn('API key', str(caught.exception))
        self.assertNotIn('secret-provider-payload', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
