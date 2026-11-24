import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'messenger/templates/lxmf_chat.html'
TRANSLATIONS = ROOT / 'messenger/static/messenger_translations.json'


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(TRANSLATIONS.read_text(encoding='utf-8'))
        cls.source = TEMPLATE.read_text(encoding='utf-8')

    def test_language_key_parity(self):
        it_keys = set(self.data['it'])
        en_keys = set(self.data['en'])
        self.assertEqual(it_keys, en_keys)
        self.assertTrue(all(isinstance(v, str) and v.strip() for v in self.data['it'].values()))
        self.assertTrue(all(isinstance(v, str) and v.strip() for v in self.data['en'].values()))

    def test_template_translation_keys_exist(self):
        attributes = re.findall(
            r'data-i18n(?:-placeholder|-title)?="([^"]+)"',
            self.source,
        )
        missing = sorted({key for key in attributes if key not in self.data['it']})
        self.assertEqual(missing, [])

    def test_selector_contains_initial_languages(self):
        self.assertIn('id="messengerLanguageSelector"', self.source)
        self.assertIn('value="it"', self.source)
        self.assertIn('value="en"', self.source)
        self.assertIn('/static/messenger_translations.json', self.source)
        self.assertIn('rns_messenger_language', self.source)


if __name__ == '__main__':
    unittest.main()
