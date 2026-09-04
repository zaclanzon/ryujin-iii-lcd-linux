import json
import subprocess
from pathlib import Path


STATIC = Path(__file__).parents[1] / "ryujin_lcd" / "static"


def test_frontend_uses_fragment_token_without_leaving_it_in_url():
    program = f"""
const fs = require('fs');
global.window = global;
global.location = {{ hash: '#token=secret-token', pathname: '/', search: '' }};
const saved = {{}};
global.sessionStorage = {{
  getItem: (key) => saved[key] || null,
  setItem: (key, value) => saved[key] = value,
}};
global.history = {{ replaceState: (_a, _b, value) => saved.url = value }};
eval(fs.readFileSync({json.dumps(str(STATIC / 'auth.js'))}, 'utf8'));
console.log(JSON.stringify({{ headers: window.ryujinAuthHeaders(), saved }}));
"""
    result = subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)

    assert output["headers"] == {"Authorization": "Bearer secret-token"}
    assert output["saved"]["url"] == "/"


def test_frontend_preserves_plus_and_ampersand_in_fragment_token():
    program = f"""
const fs = require('fs');
global.window = global;
global.location = {{ hash: '#token=secret+part&tail', pathname: '/', search: '' }};
const saved = {{}};
global.sessionStorage = {{
  getItem: (key) => saved[key] || null,
  setItem: (key, value) => saved[key] = value,
}};
global.history = {{ replaceState: (_a, _b, value) => saved.url = value }};
eval(fs.readFileSync({json.dumps(str(STATIC / 'auth.js'))}, 'utf8'));
console.log(JSON.stringify({{ headers: window.ryujinAuthHeaders() }}));
"""
    result = subprocess.run(["node", "-e", program], check=True, capture_output=True, text=True)

    assert json.loads(result.stdout)["headers"] == {"Authorization": "Bearer secret+part&tail"}
