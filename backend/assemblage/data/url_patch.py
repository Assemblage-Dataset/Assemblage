def patch_url(_url):
    """make a url cloneable"""
    return _url.replace("repos/", "").replace("api.", "")
