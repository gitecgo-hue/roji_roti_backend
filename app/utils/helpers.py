def apply_translations(doc_dict: dict, translations: dict, lang: str) -> dict:
    """
    Overwrites English fields with localized fields if the requested language exists.
    """
    if lang != "en" and translations and lang in translations:
        # Loop through the saved Hindi translations (like company_name, description)
        for key, translated_val in translations[lang].items():
            if key in doc_dict:
                # Overwrite the English dict value with the Hindi one
                doc_dict[key] = translated_val
    return doc_dict