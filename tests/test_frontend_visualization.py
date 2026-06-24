from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


class FrontendVisualizationTemplateTest(unittest.TestCase):
    def test_steps_template_supports_reasoning_and_executor_shapes(self):
        self.assertIn("stepTitle(s)", INDEX_HTML)
        self.assertIn("stepTools(s)", INDEX_HTML)
        self.assertIn("stepSource(s)", INDEX_HTML)
        self.assertIn("stepSummary(s)", INDEX_HTML)
        self.assertIn("stepTitle(step)", APP_JS)
        self.assertIn("stepTools(step)", APP_JS)
        self.assertIn("stepSource(step)", APP_JS)
        self.assertIn("stepSummary(step)", APP_JS)

    def test_token_input_is_available_outside_chat_page(self):
        topbar = INDEX_HTML.split("<!-- Chat -->", 1)[0]
        self.assertIn('class="token-input topbar-token"', topbar)
        self.assertIn('v-model="token"', topbar)


if __name__ == "__main__":
    unittest.main()
