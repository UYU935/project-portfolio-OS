from pathlib import Path
import shutil
import pytest
from portfolio_os.db import Store
from portfolio_os.domain import Evaluation, Policy, ProjectInput

@pytest.fixture
def root(tmp_path):
    source = Path(__file__).resolve().parents[1]
    for folder in ('prompts', 'config'):
        shutil.copytree(source / folder, tmp_path / folder)
    return tmp_path

@pytest.fixture
def store(tmp_path):
    s = Store(f'sqlite:///{tmp_path / "test.db"}', demo=True)
    s.initialize_demo()
    return s

@pytest.fixture
def policy():
    return Policy()

def add_project(store, slug='alpha', domain='general'):
    store.register(ProjectInput(slug=slug, name=slug, summary='A test business hypothesis, not a real market claim.', domain=domain))
    store.enroll(slug)
    return store.project(slug)

def evaluation(action='HOLD', **overrides):
    payload = dict(recommendation=action, reason='Test rationale', strongest_objection='Demand has not been demonstrated',
                   uncertainties=['Willingness to pay unknown'], evidence_ids=[], next_step='Test one hypothesis',
                   success_condition='New real-world evidence is recorded', target_stage=None, review_in_days=7)
    payload.update(overrides)
    return Evaluation(**payload)
