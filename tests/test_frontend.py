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


def test_temperature_form_apply_preserves_order_and_thresholds():
    program = r'''
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const state = { status: null, config: {
  mode: 'slideshow', hwmon: {lines: []}, slideshow: {
    source: 'gif', gif_slots: [6, 1, 2], behavior: 'temperature',
    thermal: {cpu: 'coretemp/temp1', coolant: 'rog_ryujin/temp1', warm: 55, hot: 70, stats: 90, coolant_stats: 44, hold: 15, hysteresis: 4}
  }
}};
const button = {};
const $ = () => button;
const toast = (message, kind) => { if (kind === 'err') throw new Error(message); };
const KIND_LABEL = {gif: 'Animation'};
let sent;
const api = async (method, path, body) => {
  sent = {method, path, body};
  state.config.slideshow = {...state.config.slideshow, ...body, gif_slots: body.slots};
};
const loadStatus = async () => {};
const hydrateDisplay = () => {};
const renderApplyBar = () => {};
eval(source.slice(source.indexOf('function formFromConfig'), source.indexOf('// ---- polling')));
eval(source.slice(source.indexOf('async function apply()'), source.indexOf('// ---- preview')));
state.form = formFromConfig(state.config);
apply().then(() => console.log(JSON.stringify({sent, form: state.form, disabled: button.disabled})));
'''
    # The form model ends just before the polling state declarations.
    program = program.replace("source.indexOf('// ---- polling')", "source.indexOf('let statusSeq')")
    result = subprocess.run(['node', '-e', program, str(STATIC / 'app.js')], check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)
    assert output['sent']['path'] == '/api/show'
    assert output['sent']['body']['slots'] == [6, 1, 2]
    assert output['sent']['body']['behavior'] == 'temperature'
    assert output['sent']['body']['thermal']['hold'] == 15
    assert output['form']['slideshow']['thermal']['cpu'] == 'coretemp/temp1'
    assert output['disabled'] is False
