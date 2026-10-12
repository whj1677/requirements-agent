"""Optional project domain context routes for the existing workbench."""

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from . import domain_context


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Term(Strict):
    id: str | None = None
    term: str = Field(min_length=1, max_length=100)
    meaning: str = Field(min_length=1, max_length=1000)
    status: str


class Edit(Strict):
    expected_revision: int
    background: str | None = Field(default=None, max_length=2000)
    terms: list[Term] | None = Field(default=None, max_length=100)
    selected_source_ids: list[str] | None = None


def register(app: FastAPI, store):
    @app.get('/api/projects/{pid}/domain-context')
    async def get_domain_context(pid: str):
        return domain_context.view(store.get(pid))

    def save(pid: str, body: Edit):
        fields = body.model_fields_set
        with store.edit(pid, body.expected_revision, '更新可选项目背景与本次资料选择') as (project, _):
            kwargs = {}
            if 'background' in fields:
                kwargs['background'] = body.background
            if 'terms' in fields:
                kwargs['terms'] = [term.model_dump() for term in body.terms] if body.terms is not None else []
            if 'selected_source_ids' in fields:
                kwargs['selected_source_ids'] = body.selected_source_ids
            domain_context.update(project, **kwargs)
        return domain_context.view(store.get(pid))

    @app.post('/api/projects/{pid}/domain-context')
    async def post_domain_context(pid: str, body: Edit):
        return save(pid, body)

    @app.put('/api/projects/{pid}/domain-context')
    async def put_domain_context(pid: str, body: Edit):
        return save(pid, body)
