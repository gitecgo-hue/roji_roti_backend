from deep_translator import GoogleTranslator
from typing import List, Any
import asyncio

async def translate_document_fields(document_id: str, document_model, fields_to_translate: List[str], target_lang: str = "hi"):
    """
    Translates Strings, Lists, and Deeply Nested Dictionaries in the background using deep-translator,
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
        
        # --- NEW: Recursive Helper Function with Logging ---
        async def _translate_value(val: Any, context_name: str = "unknown") -> Any:
            if isinstance(val, str):
                try:
                    # Run the sync translation in a background thread
                    trans_text = await asyncio.to_thread(translator.translate, val)
                    
                    # THE FIX: Check if the API returned a string containing the Google error
                    if trans_text and ("Error 500" in trans_text or "That’s an error" in trans_text):
                        print(f"Translation API rate-limited for '{context_name}'. Falling back.")
                        return val
                        
                    return trans_text
                except Exception as field_error:
                    print(f"Translation crashed for '{context_name}': {str(field_error)}")
                    # Always fall back to the original text if the translation crashes
                    return val
                    
            elif isinstance(val, list):
                # Recursively translate every item in the list
                return [await _translate_value(item, f"{context_name} (list item)") for item in val]
                
            elif isinstance(val, dict):
                # Recursively translate every value in the dictionary
                return {k: await _translate_value(v, f"{context_name}.{k}") for k, v in val.items()}
                
            else:
                # Numbers, booleans, and nulls stay exactly the same
                return val
        # -------------------------------------------------
        
        for field in fields_to_translate:
            original_value = getattr(doc, field, None)
            
            if original_value is not None:
                # 4. Pass the entire complex object to the recursive helper
                translated_data = await _translate_value(original_value, field)
                
                # 5. Save the safe, fully processed result to the dictionary
                doc.translations[target_lang][field] = translated_data
                
        # 6. Save the updated document back to MongoDB
        await doc.save()
        print(f"Successfully processed translations for {document_model.__name__} {document_id} to {target_lang}")
        
    except Exception as e:
        print(f"Translation task totally failed for {document_model.__name__} {document_id}: {e}")