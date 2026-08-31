from deep_translator import GoogleTranslator
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate
from typing import List, Any
import asyncio

async def translate_document_fields(document_id: str, document_model, fields_to_translate: List[str], target_lang: str = "hi"):
    """
    Translates Strings, Lists, and Deeply Nested Dictionaries in the background using deep-translator,
    with precise phonetic transliteration for names and safe fallbacks for Google errors.
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
        
        # --- Recursive Helper Function with Transliteration & Logging ---
        async def _translate_value(val: Any, context_name: str = "unknown") -> Any:
            
            # Convert Pydantic models to dicts so the script can read them
            if hasattr(val, "model_dump"):
                val = val.model_dump()
                
            if isinstance(val, str):
                # ---------------------------------------------------------
                # ONLY transliterate actual human/company names phonetically
                # ---------------------------------------------------------
                transliterate_fields = ["name", "owner_name", "company_name", "employee_name"]
                
                if target_lang == "hi" and any(field == context_name.split('.')[-1] for field in transliterate_fields):
                    try:
                        # Convert to lowercase for better ITRANS mapping, then transliterate dynamically
                        return transliterate(val.lower(), sanscript.ITRANS, sanscript.DEVANAGARI)
                    except Exception as e:
                        print(f"Transliteration failed for '{context_name}': {e}")
                        return val 
                # ---------------------------------------------------------

                # Everything else (Institutes, Summaries, Locations, Job Types) uses Google Translate:
                try:
                    # Run the sync translation in a background thread
                    trans_text = await asyncio.to_thread(translator.translate, val)
                    
                    # Check if the API returned a string containing the Google error
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
                return val
        
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