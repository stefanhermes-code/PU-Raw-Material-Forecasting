from __future__ import annotations
from dataclasses import dataclass

class LicenceGateError(RuntimeError): pass

@dataclass(frozen=True)
class ModelRecord:
    model_key:str; family:str; version:str; licence_class:str; commercial_eligible:bool; covariate_support:bool; multivariate_support:bool; quantile_support:bool; runtime_status:str

class ModelRegistry:
    def __init__(self): self._items={}
    def register(self,r:ModelRecord): self._items[r.model_key]=r; return r
    def get(self,key:str)->ModelRecord: return self._items[key]
    def all(self)->tuple[ModelRecord,...]: return tuple(self._items.values())
    def commercial_candidates(self)->tuple[ModelRecord,...]: return tuple(r for r in self._items.values() if r.commercial_eligible)

def assert_commercially_eligible(record:ModelRecord)->None:
    if not record.commercial_eligible: raise LicenceGateError(f"{record.model_key} is not commercially eligible")

def default_model_registry()->ModelRegistry:
    r=ModelRegistry()
    r.register(ModelRecord("naive","statistical","1","INTERNAL",True,False,False,False,"AVAILABLE"))
    r.register(ModelRecord("ets","statistical","additive-trend","BSD-3-Clause",True,False,False,False,"AVAILABLE"))
    r.register(ModelRecord("chronos2","foundation","amazon/chronos-2","Apache-2.0",True,True,True,True,"OPTIONAL"))
    r.register(ModelRecord("timesfm25","foundation","google/timesfm-2.5-200m-pytorch","Apache-2.0",True,True,False,True,"OPTIONAL"))
    r.register(ModelRecord("timesfm3-research","foundation","3.0","RESEARCH",False,True,True,True,"RESEARCH_ONLY"))
    return r
