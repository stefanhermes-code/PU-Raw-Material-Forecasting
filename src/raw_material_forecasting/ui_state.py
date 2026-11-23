from __future__ import annotations

import pandas as pd

from .domain import MarketIdentity
from .repositories import MarketRepository


def persistent_series_frame(repo: MarketRepository, identity: MarketIdentity) -> pd.DataFrame:
    rows=[]
    for obs in repo.list_price_observations(identity.key):
        src=repo.get_source(obs['source_key'])
        rows.append({
            'observation_date': pd.Timestamp(obs['observation_date']),
            'publication_date': pd.Timestamp(obs['publication_at']),
            'publication_date_verified': bool(obs['publication_date_verified']),
            'price_per_tonne': float(obs['value']),
            'product_family': identity.product_family,
            'grade': identity.grade,
            'origin': identity.origin,
            'destination': identity.destination,
            'price_basis': identity.price_basis,
            'packaging': identity.packaging,
            'shipment_size': identity.shipment_size,
            'currency': identity.currency,
            'source_id': obs['source_key'],
            'source_name': src.provider if src else 'UNKNOWN',
            'source_tier': src.reliability_class if src else 'UNKNOWN',
            'rights_classification': src.rights_class if src else 'UNKNOWN',
            'observation_class': obs['observation_class'],
            'identity_complete': bool(obs['identity_complete']),
        })
    return pd.DataFrame(rows).sort_values('observation_date').reset_index(drop=True) if rows else pd.DataFrame()


def intelligence_queue_frame(repo: MarketRepository) -> pd.DataFrame:
    rows=[]
    for item in repo.list_discovery_candidates():
        rows.append({
            'published_at': item['published_at'],
            'publisher': item['publisher'],
            'title': item['title'],
            'state': repo.get_candidate_state(item['candidate_id']) or 'DISCOVERED',
            'publisher_url': item['publisher_url'],
            'google_news_url': item['google_news_url'],
            'query': item['query_key'],
            'candidate_id': item['candidate_id'],
        })
    return pd.DataFrame(rows)


def conceptual_pmdi_chain_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {'Upstream / intermediate': 'Benzene', 'Path': 'Benzene → Nitrobenzene → Aniline → MDA → Crude MDI → pMDI', 'Status': 'CANDIDATE'},
        {'Upstream / intermediate': 'Nitrobenzene', 'Path': 'Nitrobenzene → Aniline → MDA → Crude MDI → pMDI', 'Status': 'CANDIDATE'},
        {'Upstream / intermediate': 'Aniline', 'Path': 'Aniline → MDA → Crude MDI → pMDI', 'Status': 'CANDIDATE'},
        {'Upstream / intermediate': 'MDA', 'Path': 'MDA → Crude MDI → pMDI', 'Status': 'CANDIDATE'},
        {'Upstream / intermediate': 'Crude MDI', 'Path': 'Crude MDI → pMDI', 'Status': 'CANDIDATE'},
    ])
