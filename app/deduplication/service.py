from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Company, CompanyContact, CompanySource
from app.db.repositories import CompanyRepository
from app.deduplication.matcher import CompanyCandidate, match_company
from app.normalization.contacts import normalize_source_company
from app.schemas.domain import NormalizedCompany, SourceCompany


class DeduplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.companies = CompanyRepository(session)

    async def upsert_source_company(self, record: SourceCompany) -> int:
        normalized = normalize_source_company(record)
        incoming = CompanyCandidate(
            company_id=None,
            source=record.source,
            source_id=record.source_id,
            normalized_name=normalized.name,
            normalized_address=normalized.address,
            phones=frozenset(normalized.phone_numbers),
            domains=frozenset(normalized.domains),
            latitude=record.latitude,
            longitude=record.longitude,
        )
        match: Company | None = None
        for candidate in await self._candidate_companies(record, normalized):
            if match_company(self._to_candidate(candidate, record), incoming) is not None:
                match = candidate
                break

        if match is None:
            created = await self.companies.create(record, normalized)
            await self._refresh_contact_totals(created)
            return created.id

        await self.companies.attach_source(match.id, record)
        for contact in normalized.contacts:
            await self.companies.upsert_contact(
                match.id,
                contact.type,
                contact.value,
                contact.normalized_value,
                record.source,
                is_primary=contact.is_primary,
            )
        self._merge_canonical_fields(match, record, normalized)
        await self._refresh_contact_totals(match)
        await self.session.flush()
        return match.id

    async def _candidate_companies(
        self,
        record: SourceCompany,
        normalized: NormalizedCompany,
    ) -> list[Company]:
        conditions: list[ColumnElement[bool]] = [
            Company.id.in_(
                select(CompanySource.company_id).where(
                    CompanySource.source == record.source,
                    CompanySource.source_id == record.source_id,
                )
            )
        ]
        if normalized.phone_numbers:
            conditions.append(
                Company.id.in_(
                    select(CompanyContact.company_id).where(
                        CompanyContact.normalized_value.in_(normalized.phone_numbers)
                    )
                )
            )
        if normalized.domains:
            conditions.append(Company.registrable_domain.in_(normalized.domains))
        if normalized.address:
            conditions.append(
                and_(
                    Company.normalized_name == normalized.name,
                    Company.normalized_address == normalized.address,
                )
            )
        if record.latitude is not None and record.longitude is not None:
            conditions.append(
                and_(
                    Company.city == record.city,
                    Company.latitude.between(record.latitude - 0.002, record.latitude + 0.002),
                    Company.longitude.between(
                        record.longitude - 0.003,
                        record.longitude + 0.003,
                    ),
                )
            )
        statement = (
            select(Company)
            .where(or_(*conditions))
            .options(
                selectinload(Company.sources),
                selectinload(Company.contacts),
            )
            .with_for_update()
        )
        return list((await self.session.scalars(statement)).unique().all())

    @staticmethod
    def _to_candidate(company: Company, incoming: SourceCompany) -> CompanyCandidate:
        matching_source = next(
            (item for item in company.sources if item.source is incoming.source),
            company.sources[0],
        )
        return CompanyCandidate(
            company_id=company.id,
            source=matching_source.source,
            source_id=matching_source.source_id,
            normalized_name=company.normalized_name,
            normalized_address=company.normalized_address,
            phones=frozenset(
                contact.normalized_value
                for contact in company.contacts
                if contact.type.value == "PHONE"
            ),
            domains=frozenset((company.registrable_domain,) if company.registrable_domain else ()),
            latitude=company.latitude,
            longitude=company.longitude,
        )

    @staticmethod
    def _merge_canonical_fields(
        company: Company,
        record: SourceCompany,
        normalized: NormalizedCompany,
    ) -> None:
        if len(record.name) > len(company.name):
            company.name = record.name
            company.normalized_name = normalized.name
        if record.address and (not company.address or len(record.address) > len(company.address)):
            company.address = record.address
            company.normalized_address = normalized.address
        if company.primary_category is None and record.categories:
            company.primary_category = record.categories[0]
        if company.canonical_website is None and normalized.websites:
            company.canonical_website = normalized.websites[0]
            company.registrable_domain = normalized.domains[0] if normalized.domains else None
        if company.latitude is None and record.latitude is not None:
            company.latitude = record.latitude
            company.longitude = record.longitude
        if (record.reviews_count or 0) > (company.reviews_count or 0):
            company.rating = record.rating
            company.reviews_count = record.reviews_count

    async def _refresh_contact_totals(self, company: Company) -> None:
        contact_count = await self.session.scalar(
            select(func.count())
            .select_from(CompanyContact)
            .where(CompanyContact.company_id == company.id)
        )
        company.contact_count = int(contact_count or 0)
        company.contacts_found = company.contact_count > 0
