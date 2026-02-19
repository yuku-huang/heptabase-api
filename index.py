from flask import Flask, request
from flask_caching import Cache
import requests
import time
import json
from flask_cors import CORS
from urllib.parse import urlparse
import re

# yuku 的数字花园 ID
HEPTABASE_WHITEBOARD_ID = 'd63aeb7e4704beae2c3f8f343b7fff491d76d41ee3199c78d4c4115b70c5f83b'

# 存储 heptabase base 数据
HEPTABASE_DATA = {'result': 'erro', 'data': {}, 'time': ''}
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def get_whiteborad_id():
    '''
    获取 whiteborad ID
    '''
    whiteboard_id = request.args.get('whiteboard_id')
    if(whiteboard_id):
        return whiteboard_id
    else:
        return None


def get_hepta_data(whiteboard_id):
    '''
    获取 heptabase 数据
    '''
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7,zh-CN;q=0.6'
    }
    req = requests.get(
        'https://api.heptabase.com/v1/whiteboard-sharing/?secret=' + whiteboard_id,
        headers=headers
    )
    req.encoding = 'utf-8'  # Manually set the encoding


    if(req.status_code != 200):
        return {'code': req.status_code, 'data': ''}
    else:
        return {'code': req.status_code, 'data': json.loads(req.text)}


def parse_bool(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def is_uuid(value):
    return isinstance(value, str) and bool(UUID_PATTERN.match(value))


def get_payload_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get('data'), dict):
        return payload['data']
    return payload if isinstance(payload, dict) else {}


def get_cards_container(payload_data):
    cards = payload_data.get('cards')
    if isinstance(cards, list):
        return cards

    for value in payload_data.values():
        if not isinstance(value, list) or len(value) == 0:
            continue
        sample = value[0]
        if isinstance(sample, dict) and 'id' in sample and 'content' in sample:
            return value
    return []


def get_default_owner_id(payload_data):
    whiteboards = payload_data.get('whiteboards')
    if isinstance(whiteboards, list) and len(whiteboards) > 0:
        first = whiteboards[0]
        if isinstance(first, dict):
            return first.get('createdBy')
    return None


def resolve_image_src(attrs, owner_id):
    src = attrs.get('src')
    if isinstance(src, str) and src.strip():
        return src

    file_id = attrs.get('fileId')
    if not file_id or not owner_id:
        return src

    return f'https://media.heptabase.com/v1/images/{owner_id}/{file_id}/image.png'


def walk_and_resolve_images(node, owner_id, image_urls):
    if isinstance(node, dict):
        if node.get('type') == 'image':
            attrs = node.get('attrs') if isinstance(node.get('attrs'), dict) else {}
            resolved_src = resolve_image_src(attrs, owner_id)
            if resolved_src:
                attrs['src'] = resolved_src
                node['attrs'] = attrs
                image_urls.append(resolved_src)

        content = node.get('content')
        if isinstance(content, list):
            for child in content:
                walk_and_resolve_images(child, owner_id, image_urls)
    elif isinstance(node, list):
        for child in node:
            walk_and_resolve_images(child, owner_id, image_urls)


def parse_card_content(card, default_owner_id=None):
    if not isinstance(card, dict):
        return None, []

    raw_content = card.get('content')
    if not isinstance(raw_content, str):
        return None, []

    try:
        doc = json.loads(raw_content)
    except (TypeError, json.JSONDecodeError):
        return None, []

    owner_id = card.get('createdBy') or default_owner_id
    image_urls = []
    walk_and_resolve_images(doc, owner_id, image_urls)

    unique_image_urls = list(dict.fromkeys(image_urls))
    return doc, unique_image_urls


def extract_card_and_block_ids(url):
    try:
        parsed_url = urlparse(url)
    except ValueError:
        return None, None

    segments = [seg for seg in parsed_url.path.split('/') if seg]
    card_id = None
    for idx, segment in enumerate(segments):
        if segment == 'card' and idx + 1 < len(segments):
            card_id = segments[idx + 1]
            break

    block_id = parsed_url.fragment if parsed_url.fragment else None
    return card_id, block_id


def find_block_by_id(node, block_id):
    if isinstance(node, dict):
        attrs = node.get('attrs')
        if isinstance(attrs, dict) and attrs.get('id') == block_id:
            return node

        content = node.get('content')
        if isinstance(content, list):
            for child in content:
                found = find_block_by_id(child, block_id)
                if found:
                    return found
    elif isinstance(node, list):
        for child in node:
            found = find_block_by_id(child, block_id)
            if found:
                return found

    return None


def resolve_images_for_all_cards(payload):
    payload_data = get_payload_data(payload)
    cards = get_cards_container(payload_data)
    default_owner_id = get_default_owner_id(payload_data)

    for card in cards:
        doc, image_urls = parse_card_content(card, default_owner_id)
        if doc is None:
            continue

        card['content'] = json.dumps(doc, ensure_ascii=False)
        card['resolvedImageUrls'] = image_urls

    return payload


app = Flask(__name__)
app.config['REQUEST_TIMEOUT'] = 60
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})
CORS(app, supports_credentials=True)


@app.route('/')
# @cache.cached(timeout=30, query_string=True)  # 设置缓存的超时时间（以秒为单位）
def home():
    global HEPTABASE_DATA

    whiteboard_id = get_whiteborad_id()
    cache_key = f'{whiteboard_id}'

    if cache.get(cache_key):  # 如果缓存存在，则直接从缓存中提取数据
        return cache.get(cache_key)['data']
    else:

        if(whiteboard_id):
            req = get_hepta_data(whiteboard_id)
        else:
            # 返回 Jiang 的数字花园数据
            
            # with open('data.json', mode='r') as my_file:
            #     req = my_file.read()
            #     req = json.loads(req)
            
            req = get_hepta_data(HEPTABASE_WHITEBOARD_ID)
            
            # with open('data.json', 'w', encoding='utf-8') as f:
            #     json.dump(req, f, ensure_ascii=False, indent=4)
                

        payload = req['data']
        if parse_bool(request.args.get('resolve_images')):
            payload = resolve_images_for_all_cards(payload)

        HEPTABASE_DATA = {'result': 'success', 'code': req['code'],
                          'data': payload, 'time': int(time.time())}
        return HEPTABASE_DATA


@app.route('/card')
def card():
    whiteboard_id = request.args.get('whiteboard_id') or HEPTABASE_WHITEBOARD_ID
    card_id = request.args.get('card_id')
    block_id = request.args.get('block_id')
    article_url = request.args.get('url')

    if article_url:
        extracted_card_id, extracted_block_id = extract_card_and_block_ids(article_url)
        if not card_id:
            card_id = extracted_card_id
        if not block_id:
            block_id = extracted_block_id

    if not is_uuid(card_id):
        return {
            'result': 'error',
            'message': 'Please provide a valid card_id or Heptabase card URL.'
        }, 400

    req = get_hepta_data(whiteboard_id)
    if req['code'] != 200:
        return {
            'result': 'error',
            'message': 'Failed to fetch data from Heptabase.',
            'code': req['code']
        }, req['code']

    payload_data = get_payload_data(req['data'])
    cards = get_cards_container(payload_data)
    default_owner_id = get_default_owner_id(payload_data)
    card_data = next((c for c in cards if c.get('id') == card_id), None)

    if not card_data:
        return {
            'result': 'error',
            'message': 'Card not found in this whiteboard data.'
        }, 404

    parsed_doc, image_urls = parse_card_content(card_data, default_owner_id)
    if parsed_doc is None:
        return {
            'result': 'error',
            'message': 'Card content is not a valid rich-text JSON document.'
        }, 422

    focus_block = None
    if block_id:
        focus_block = find_block_by_id(parsed_doc, block_id)

    return {
        'result': 'success',
        'cardId': card_id,
        'blockId': block_id,
        'title': card_data.get('title'),
        'images': image_urls,
        'content': parsed_doc,
        'focusBlock': focus_block
    }


@app.route('/update')
def update():
    '''
    获取 hepta 数据存储到全局变量中
    '''
    global HEPTABASE_DATA

    whiteboard_id = get_whiteborad_id()
    cache_key = f'{whiteboard_id}'

    req_json = get_hepta_data(whiteboard_id)
    HEPTABASE_DATA = {'result': 'success',
                      'data': req_json, 'time': int(time.time())}

    cache.set(cache_key, HEPTABASE_DATA, timeout=3600)  # 更新缓存并设置新的超时时间

    return HEPTABASE_DATA


@app.route('/about')
def about():
    return 'About Page Route'


@app.route('/portfolio')
def portfolio():
    return 'Portfolio Page Route'


@app.route('/contact')
def contact():
    return 'Contact Page Route'


@app.route('/api')
def api():
    with open('data.json', mode='r') as my_file:
        text = my_file.read()
        return text


if __name__ == '__main__':
    app.run(debug=True)
