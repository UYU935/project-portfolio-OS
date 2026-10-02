import asyncio
import json
import socket
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError
from portfolio_os.cli import run_demo
from portfolio_os.config import Config
from portfolio_os.db import Store
from portfolio_os.domain import Evaluation, Finding, Policy, Research, RuleError, public_url, strict_json_schema
from portfolio_os.provider import OpenAIProvider, ProviderError
from portfolio_os.reports import make_report
from portfolio_os.workspace import safe_write
from conftest import evaluation

# All credentials in these tests are nonfunctional fixtures, not real keys.
def response_for(value, *, search=False, url='https://example.com/source', status='completed'):
    output=[]
    if search:
        output.append({'type':'web_search_call','status':'completed','action':{'sources':[{'url':url,'type':'url'}]}})
    output.append({'type':'message','content':[{'type':'output_text','text':value.model_dump_json(),'annotations':[]}]})
    return {'id':'resp_fixture','status':status,'output':output,'usage':{'input_tokens':100,'output_tokens':50}}

def test_responses_contract_and_no_secrets_in_prompt(root, policy):
    calls=[]
    def handler(request):
        body=json.loads(request.content); calls.append(body)
        assert request.url == 'https://api.openai.com/v1/responses'
        assert request.headers['Authorization'] == 'Bearer sk-test-secret'
        assert 'sk-test-secret' not in request.content.decode()
        assert body['store'] is False
        assert body['text']['format']['strict'] is True
        assert body['model'] == 'explicit-model'
        assert 'tools' not in body
        return httpx.Response(200,json=response_for(evaluation()),headers={'x-request-id':'req_fixture'})
    provider=OpenAIProvider('sk-test-secret','explicit-model',root,transport=httpx.MockTransport(handler))
    out=asyncio.run(provider.evaluate({'project':{'summary':'sanitized'}},policy))
    assert out.input_tokens == 100 and out.request_id == 'req_fixture' and len(calls)==1

def test_search_only_accepts_retrieved_source_urls(root, policy):
    value=Research(summary='Test',findings=[Finding(statement='Test claim',url='https://invented.example/claim',
        title='Claim',limitation='Not verified')],counterargument='Unknown demand',unanswered=[])
    provider=OpenAIProvider('test','model',root,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response_for(value,search=True))))
    with pytest.raises(ProviderError,match='SOURCE_NOT_IN_RETRIEVED_RESULTS'):
        asyncio.run(provider.research({}, {}, policy))

def test_actual_search_required_even_for_empty_findings(root, policy):
    value=Research(summary='No data',findings=[],counterargument='No evidence',unanswered=['Unknown'])
    provider=OpenAIProvider('test','model',root,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response_for(value))))
    with pytest.raises(ProviderError,match='NO_WEB_SEARCH_PERFORMED'):
        asyncio.run(provider.research({}, {}, policy))

def test_valid_search_provenance_is_persistable(root, policy):
    value=Research(summary='Test',findings=[Finding(statement='Test claim',url='https://example.com/source',
        title='Claim',limitation='Not customer evidence')],counterargument='Unknown demand',unanswered=[])
    def handler(request):
        body=json.loads(request.content)
        assert body['max_tool_calls']==1
        assert body['include']==['web_search_call.action.sources']
        assert body['tools'][0]['type']=='web_search'
        return httpx.Response(200,json=response_for(value,search=True))
    provider=OpenAIProvider('test','model',root,transport=httpx.MockTransport(handler))
    out=asyncio.run(provider.research({}, {}, policy))
    assert out.sources == ['https://example.com/source']

def test_invalid_schema_cannot_add_an_external_action():
    data=evaluation().model_dump(); data['send_email']='attacker@example.com'
    with pytest.raises(ValidationError): Evaluation.model_validate(data)

@pytest.mark.parametrize('status',[401,403,429,500])
def test_provider_errors_never_retry_or_echo_response(root, policy, status):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(status,json={'error':'SECRET_DATA_DO_NOT_LOG'})
    provider=OpenAIProvider('secret','model',root,transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as e:
        asyncio.run(provider.evaluate({}, policy))
    assert 'SECRET_DATA' not in str(e.value) and len(calls)==1
    assert e.value.uncertain == (status >= 500)

def test_incomplete_response_is_not_success(root,policy):
    provider=OpenAIProvider('test','model',root,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response_for(evaluation(),status='incomplete'))))
    with pytest.raises(ProviderError,match='NOT_COMPLETED'):
        asyncio.run(provider.evaluate({},policy))

def test_prompt_input_is_bounded_before_network(root,policy):
    def no_call(r): raise AssertionError('Network should not have been used')
    provider=OpenAIProvider('test','model',root,transport=httpx.MockTransport(no_call))
    with pytest.raises(ProviderError,match='INPUT_TOO_LARGE'):
        asyncio.run(provider.evaluate({'large':'x'*policy.max_input_characters},policy))

def test_no_model_or_key_fallback(root):
    with pytest.raises(RuleError): OpenAIProvider('', 'model', root)
    with pytest.raises(RuleError): OpenAIProvider('secret', '', root)

def test_schema_nullable_fields_are_required():
    schema=strict_json_schema(Evaluation)
    assert set(schema['required'])==set(schema['properties'])
    assert schema['additionalProperties'] is False
    assert 'target_stage' in schema['required']

@pytest.mark.parametrize('url',['http://localhost/','http://127.0.0.1/','http://10.1.2.3/','http://user:pass@example.com/','file:///etc/passwd','javascript:alert(1)'])
def test_private_or_unsafe_urls_rejected(url):
    with pytest.raises(ValueError): public_url(url)

def test_path_traversal_and_absolute_paths_blocked(root):
    with pytest.raises(RuleError): safe_write(root,'../escape.txt','x')
    with pytest.raises(RuleError): safe_write(root,'/tmp/escape.txt','x')

def test_symlink_path_blocked(root,tmp_path):
    target=tmp_path/'target';target.mkdir()
    (root/'linked').symlink_to(target,target_is_directory=True)
    with pytest.raises(RuleError):safe_write(root,'linked/x.txt','x')

def test_report_escapes_untrusted_project_text(store):
    from portfolio_os.domain import ProjectInput
    store.register(ProjectInput(slug='unsafe',name='<script>alert(1)</script>',summary='Input'))
    html=make_report(store.export())
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html
    assert "default-src 'none'" in html

def test_demo_never_connects_to_a_network(root,monkeypatch):
    def blocked(*a,**kw):raise AssertionError('No network permitted')
    monkeypatch.setattr(socket,'create_connection',blocked)
    report=run_demo(root,root/'demo-test')
    assert report.exists()
    html=report.read_text()
    assert 'DEMO' in html and '検索・Codexは実行していません' in html
    data=json.loads((report.parent/'state.json').read_text())
    assert data['demo'] is True and data['paused'] is True
    assert len(data['tasks'])==2
    assert all(t['status']=='DONE' for t in data['tasks'])
    assert len([a for a in data['approvals'] if a['status']=='PENDING'])==1

def test_demo_cannot_point_at_supabase():
    with pytest.raises(RuleError): Store('postgresql+psycopg://bad',demo=True)

def test_production_cannot_silently_use_sqlite():
    with pytest.raises(RuleError): Store('sqlite://',demo=False)

def test_policy_never_enables_codex_automatically():
    with pytest.raises(ValidationError): Policy(allow_codex_execution=True)
    with pytest.raises(ValidationError): Policy(human_wip_limit=4)


@pytest.mark.parametrize("error_obj,expected", [
    ({"type":"insufficient_quota"}, "OPENAI_QUOTA_EXCEEDED"),
    ({"code":"credit_balance_exhausted"}, "OPENAI_CREDIT_BALANCE_EXHAUSTED"),
    ({"code":"organization_usage_limit_exceeded"}, "OPENAI_ORGANIZATION_USAGE_LIMIT_EXCEEDED"),
    ({"code":"organization_spend_limit_exceeded"}, "OPENAI_ORGANIZATION_SPEND_LIMIT_EXCEEDED"),
    ({"code":"project_spend_limit_exceeded"}, "OPENAI_PROJECT_SPEND_LIMIT_EXCEEDED"),
    ({}, "OPENAI_RATE_LIMITED"),
])
def test_429_is_safely_classified(root, policy, error_obj, expected):
    provider=OpenAIProvider(
        "secret","model",root,
        transport=httpx.MockTransport(
            lambda r:httpx.Response(429,json={"error":{**error_obj,"message":"PRIVATE_BILLING_DETAIL"}})
        )
    )
    with pytest.raises(ProviderError) as e:
        asyncio.run(provider.evaluate({}, policy))
    assert e.value.code == expected
    assert "PRIVATE_BILLING_DETAIL" not in str(e.value)
