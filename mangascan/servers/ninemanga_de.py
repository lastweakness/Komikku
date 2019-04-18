import requests

from mangascan.servers.ninemanga import headers
from mangascan.servers.ninemanga import Ninemanga

server_id = 'ninemanga_de'
server_name = 'Nine Manga'
server_lang = 'de'

session = None


class Ninemanga_de(Ninemanga):
    id = server_id
    name = server_name
    lang = server_lang

    base_url = 'http://de.ninemanga.com'
    search_url = base_url + '/search/ajax/'
    manga_url = base_url + '/manga/{0}.html'
    chapter_url = base_url + '/chapter/{0}/{1}'
    page_url = chapter_url
    cover_url = 'https://img.wiemanga.com{0}'

    def __init__(self):
        global session

        if session is None:
            session = requests.Session()
            session.headers = headers