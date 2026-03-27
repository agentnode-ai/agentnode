from langchain.tools import BaseTool


class TextCleaner(BaseTool):
    name = "text_cleaner"
    description = "Clean and normalize text for NLP processing"

    def _run(self, text: str) -> str:
        import re
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w\s]", "", text)
        return text
