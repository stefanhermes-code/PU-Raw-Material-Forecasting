from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import MarketIdentityModel, PriceObservationModel, SourceRegistryModel
from app.domain.schemas import MarketIdentity
from app.domain.series_key import comparability_group
from app.persistence.identity import ensure_identity
from app.sources.registry import SourceRegistry


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _source_values(source) -> dict[str, Any]:
    return {
        'publisher': source.publisher,
        'source_name': source.source_name,
        'base_url': source.base_url,
        'rights_class': source.rights_class,
        'rights_status': source.rights_status.value,
        'collection_method': source.collection_method,
        'refresh_frequency': source.refresh_frequency,
        'active': source.active,
        'reliability_level': source.reliability_level,
        'notes': source.notes or None,
    }


def _ensure_sources(session, registry: SourceRegistry) -> None:
    for source in registry.list():
        row = session.scalar(select(SourceRegistryModel).where(SourceRegistryModel.source_id == source.source_id))
        values = _source_values(source)
        if row is None:
            session.add(SourceRegistryModel(source_id=source.source_id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    session.flush()


def _ensure_identity(session, item: dict[str, Any]) -> MarketIdentityModel:
    """Map a bootstrap row onto a MarketIdentity, then hand it to the one shared upsert.

    This file used to carry its own copy of the find-or-create. When v0.4.70 taught the live path to
    derive origin from the Incoterm and this copy was not, the two stopped agreeing on what counts as
    the same series - and every governed identity was minted twice on the next start. Building the
    identity is this file's job; matching and writing the row is not.
    """
    identity = MarketIdentity(
        product_family=item['product_family'],
        product_grade_or_chemistry=item.get('product_grade_or_chemistry'),
        origin=item.get('origin'),
        destination=item.get('destination'),
        market_geography=item.get('market_geography'),
        incoterm_or_commercial_basis=item.get('commercial_basis'),
        packaging=item.get('packaging'),
        currency=item['currency'],
        unit=item['unit'],
        observation_type=item['observation_type'],
        comparability_group=comparability_group(item),
    )
    # Gated like every other caller. These are already-governed fields, so a refusal here means
    # the bootstrap file disagrees with the vocabulary - which is a fault to see, not to skip.
    return ensure_identity(session, identity)


def _evidence_id(item: dict[str, Any]) -> str:
    key = '|'.join(
        [
            str(item['source_id']),
            str(item['observation_date']),
            f"{float(item['price']):.8f}",
            str(item.get('source_url') or ''),
        ]
    )
    return 'bootstrap:' + sha256(key.encode('utf-8')).hexdigest()


def bootstrap_file(session_factory, registry: SourceRegistry, path: str | Path) -> dict[str, int]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    observations = payload.get('observations') or []
    inserted = 0
    with session_factory() as session:
        _ensure_sources(session, registry)
        for item in observations:
            identity = _ensure_identity(session, item)
            evidence_id = _evidence_id(item)
            observation_date = _dt(item['observation_date'])
            # Match the *observation*, not the bootstrap evidence id: once live collection has
            # picked up a day the bootstrap file also covers, the two ids differ and the row was
            # inserted twice (4 such pairs found in the live database on 2026-09-09).
            existing = session.scalar(
                select(PriceObservationModel).where(
                    PriceObservationModel.identity_id == identity.id,
                    PriceObservationModel.source_id == item['source_id'],
                    PriceObservationModel.observation_date == observation_date,
                    PriceObservationModel.price == float(item['price']),
                )
            )
            if existing is not None:
                continue
            publication_date = _dt(item.get('publication_date')) or observation_date
            session.add(
                PriceObservationModel(
                    identity_id=identity.id,
                    source_id=item['source_id'],
                    evidence_id=evidence_id,
                    price=float(item['price']),
                    observation_date=observation_date,
                    publication_date=publication_date,
                    available_from=publication_date or observation_date or datetime.now(timezone.utc),
                    weight=float(item.get('weight', 1.0)),
                    currency=str(item.get('currency') or '').strip().upper(),
                )
            )
            inserted += 1
        session.commit()
    return {'observations_inserted': inserted, 'observations_seen': len(observations)}
