from beanie import Document
from pydantic import Field
from typing import Dict, Optional, Any

class TranslatableDocument(Document):
    # Stores translations. Format: {"hi": {"title": "...", "desc": "..."}}
    translations: Optional[Dict[str, Dict[str, str]]] = Field(default_factory=dict)

    def localize(self, lang_code: str) -> Dict[str, Any]:
        """
        Swaps the English text with the translated text based on the lang_code.
        """
        doc_dict = self.model_dump()
        
        if lang_code == "en" or not self.translations or lang_code not in self.translations:
            doc_dict.pop("translations", None)
            return doc_dict

        lang_data = self.translations[lang_code]
        for field_name, translated_text in lang_data.items():
            if field_name in doc_dict:
                doc_dict[field_name] = translated_text
                
        doc_dict.pop("translations", None)
        return doc_dict