from deep_translator import GoogleTranslator
from typing import List
import asyncio

async def translate_document_fields(document_id: str, document_model, fields_to_translate: List[str], target_lang: str = "hi"):
    """
    Translates specified fields in the background using deep-translator,
    with safe fallbacks for Google 500 errors and rate limits.
    """
    # 1. Fetch the document
    doc = await document_model.get(document_id)
    if not doc:
        return

    # 2. Initialize the translation dictionary
    if not doc.translations:
        doc.translations = {}
    if target_lang not in doc.translations:
        doc.translations[target_lang] = {}

    try:
        # 3. Set up the deep-translator instance
        translator = GoogleTranslator(source='auto', target=target_lang)
        
        for field in fields_to_translate:
            original_text = getattr(doc, field, None)
            
            if original_text and isinstance(original_text, str):
                try:
                    # 4. Run the sync translation in a background thread
                    translated_text = await asyncio.to_thread(translator.translate, original_text)
                    
                    # 5. THE FIX: Check if the API returned a string containing the Google error
                    if translated_text and ("Error 500 (Server Error)" in translated_text or "That’s an error" in translated_text):
                        print(f"Translation API rate-limited for field '{field}'. Falling back to original text.")
                        translated_text = original_text 
                        
                except Exception as field_error:
                    print(f"Translation crashed for field '{field}': {str(field_error)}")
                    # Always fall back to the original text if the translation crashes
                    translated_text = original_text
                
                # 6. Save the safe result to the dictionary
                doc.translations[target_lang][field] = translated_text
                
        # 7. Save the updated document back to MongoDB
        await doc.save()
        print(f"Successfully processed translations for {document_model.__name__} {document_id} to {target_lang}")
        
    except Exception as e:
        print(f"Translation task totally failed for {document_model.__name__} {document_id}: {e}")