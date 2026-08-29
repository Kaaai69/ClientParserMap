from sqlalchemy import func, select

from app.core.enums import ContactType, SourceName
from app.db.models import CompanyContact, CompanyContactSource, JobOutbox
from app.db.repositories import CompanyRepository, SearchJobRepository
from app.schemas.domain import NormalizedCompany, SearchCriteria, SourceCompany


async def test_create_job_writes_outbox_atomically(session) -> None:
    repo = SearchJobRepository(session)

    job = await repo.create_with_outbox(
        SearchCriteria(city="Москва", query="детейлинг"),
        (SourceName.GOOGLE,),
    )
    await session.commit()

    outbox = await session.scalar(select(JobOutbox).where(JobOutbox.search_job_id == job.id))
    assert outbox is not None
    assert outbox.published_at is None
    assert [item.source for item in job.source_states] == [SourceName.GOOGLE]


async def test_contact_upsert_merges_provenance(session) -> None:
    repo = CompanyRepository(session)
    record = SourceCompany(
        source=SourceName.GOOGLE,
        source_id="google-1",
        name="Авто Детейлинг",
        city="Москва",
        phones=("+79991234567",),
    )
    company = await repo.create(record, NormalizedCompany(name="авто детейлинг"))

    first = await repo.upsert_contact(
        company.id,
        ContactType.PHONE,
        "+79991234567",
        "+79991234567",
        SourceName.GOOGLE,
    )
    second = await repo.upsert_contact(
        company.id,
        ContactType.PHONE,
        "8 999 123-45-67",
        "+79991234567",
        SourceName.TWO_GIS,
    )
    await session.commit()

    assert first.id == second.id
    assert await session.scalar(select(func.count()).select_from(CompanyContact)) == 1
    evidence = (
        await session.scalars(
            select(CompanyContactSource).where(CompanyContactSource.contact_id == first.id)
        )
    ).all()
    assert {item.source for item in evidence} == {"google", "2gis"}


async def test_linking_company_to_same_job_is_idempotent(session) -> None:
    jobs = SearchJobRepository(session)
    companies = CompanyRepository(session)
    job = await jobs.create_with_outbox(
        SearchCriteria(city="Москва", query="детейлинг"),
        (SourceName.GOOGLE,),
    )
    company = await companies.create(
        SourceCompany(
            source=SourceName.GOOGLE,
            source_id="google-1",
            name="Авто Детейлинг",
            city="Москва",
        ),
        NormalizedCompany(name="авто детейлинг"),
    )

    first = await companies.link_job(job.id, company.id)
    second = await companies.link_job(job.id, company.id)

    assert first.id == second.id
