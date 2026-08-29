from app.core.enums import ContactType, SourceName
from app.db.repositories import CompanyRepository
from app.deduplication.service import DeduplicationService
from app.schemas.domain import SourceCompany


async def test_merge_keeps_all_sources_and_contacts(session) -> None:
    service = DeduplicationService(session)
    google_record = SourceCompany(
        source=SourceName.GOOGLE,
        source_id="google-1",
        name="Авто Детейлинг",
        city="Москва",
        address="ул. Тверская, 10",
        phones=("+7 999 123-45-67",),
        rating=4.8,
        reviews_count=25,
    )
    two_gis_record = SourceCompany(
        source=SourceName.TWO_GIS,
        source_id="2gis-1",
        name="Авто Детейлинг Москва",
        city="Москва",
        address="Москва, Тверская улица, 10",
        phones=("89991234567",),
        emails=("sales@example.ru",),
        rating=4.9,
        reviews_count=137,
    )

    company_id = await service.upsert_source_company(google_record)
    merged_id = await service.upsert_source_company(two_gis_record)
    await session.commit()
    company = await CompanyRepository(session).get_detail(company_id)

    assert merged_id == company_id
    assert company is not None
    assert {source.source for source in company.sources} == {
        SourceName.GOOGLE,
        SourceName.TWO_GIS,
    }
    assert {(contact.type, contact.normalized_value) for contact in company.contacts} == {
        (ContactType.PHONE, "+79991234567"),
        (ContactType.EMAIL, "sales@example.ru"),
    }
    assert (company.rating, company.reviews_count) == (4.9, 137)


async def test_same_source_id_is_idempotent(session) -> None:
    service = DeduplicationService(session)
    record = SourceCompany(
        source=SourceName.GOOGLE,
        source_id="google-1",
        name="Авто Детейлинг",
        city="Москва",
    )

    first = await service.upsert_source_company(record)
    second = await service.upsert_source_company(record)

    assert first == second
