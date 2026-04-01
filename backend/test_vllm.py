import urllib.request
import json
req = urllib.request.Request('http://vllm:6000/v1/models')
try:
    with urllib.request.urlopen(req) as f:
        print(f.status)
        data = json.loads(f.read())
        print('Success:', data)
except Exception as e:
    print('Error:', e)