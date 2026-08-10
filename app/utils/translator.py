from googletrans import Translator
from typing import List

async def translate_document_fields(document_id: str, document_model, fields_to_translate: List[str], target_lang: str = "hi"):
    """
    Translates specified fields in the background and saves them to the document's translation dictionary.
    """
    doc = await document_model.get(document_id)
    if not doc:
        return

    translator = Translator()
    
    if not doc.translations:
        doc.translations = {}
    if target_lang not in doc.translations:
        doc.translations[target_lang] = {}

    try:
        for field in fields_to_translate:
            original_text = getattr(doc, field, None)
            
            if original_text and isinstance(original_text, str):
                translated = await translator.translate(original_text, dest=target_lang)
                doc.translations[target_lang][field] = translated.text
                
        await doc.save()
        print(f"Translated {document_model.__name__} {document_id} to {target_lang}")
        
    except Exception as e:
        print(f"Translation failed for {document_model.__name__} {document_id}: {e}")