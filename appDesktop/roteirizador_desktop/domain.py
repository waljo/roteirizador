from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any, Dict, List, Optional


VERSION_PROGRAMACAO = "programacao"
VERSION_CL = "cl_oficial"
VERSION_TYPES = (VERSION_PROGRAMACAO, VERSION_CL)


def utc_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_special_code(code: str) -> str:
    cleaned = code.strip().upper()
    match = re.match(r"^([A-Z]+)-?0*([0-9]+)$", cleaned)
    if match:
        return f"{match.group(1)}-{int(match.group(2))}"
    return cleaned


@dataclass
class FleetVessel:
    nome: str
    tipo: str
    capacidade: int
    velocidade: float
    ativa: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialDemand:
    codigo: str
    origem: str
    destino: str
    horario: str
    descricao: str = ""
    excluir_do_solver: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DemandItem:
    plataforma: str
    tmib: int = 0
    m9: int = 0
    prioridade: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AvailableBoat:
    nome: str
    hora_saida: str = ""
    rota_fixa: str = ""
    disponivel: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialExecution:
    codigo: str
    embarcacao: str = ""
    horario: str = ""
    trajeto: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperationVersion:
    versao: str
    usuario: str
    criado_em: str
    tipo_origem: str = "formulario"
    troca_turma: bool = False
    rendidos_m9: int = 0
    embarcacoes_disponiveis: List[AvailableBoat] = field(default_factory=list)
    demanda: List[DemandItem] = field(default_factory=list)
    execucoes_especiais: List[SpecialExecution] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "versao": self.versao,
            "usuario": self.usuario,
            "criado_em": self.criado_em,
            "tipo_origem": self.tipo_origem,
            "troca_turma": self.troca_turma,
            "rendidos_m9": self.rendidos_m9,
            "embarcacoes_disponiveis": [item.to_dict() for item in self.embarcacoes_disponiveis],
            "demanda": [item.to_dict() for item in self.demanda],
            "execucoes_especiais": [item.to_dict() for item in self.execucoes_especiais],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationVersion":
        return cls(
            versao=data["versao"],
            usuario=data.get("usuario", ""),
            criado_em=data.get("criado_em", utc_now_iso()),
            tipo_origem=data.get("tipo_origem", "formulario"),
            troca_turma=bool(data.get("troca_turma", False)),
            rendidos_m9=int(data.get("rendidos_m9", 0)),
            embarcacoes_disponiveis=[
                AvailableBoat(**item) for item in data.get("embarcacoes_disponiveis", [])
            ],
            demanda=[DemandItem(**item) for item in data.get("demanda", [])],
            execucoes_especiais=[
                SpecialExecution(**item) for item in data.get("execucoes_especiais", [])
            ],
        )


@dataclass
class OperationMetadata:
    operacao_id: str
    data_operacao: str
    criada_em: str
    status: str = "em_andamento"
    display_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationMetadata":
        return cls(**data)

    def label(self) -> str:
        return self.display_name or self.operacao_id


@dataclass
class AppConfig:
    storage_root: str = ""
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        return cls(
            storage_root=data.get("storage_root", ""),
            version=int(data.get("version", 1)),
        )


@dataclass
class OperationalConfig:
    frota: List[FleetVessel]
    unidades: List[str]
    gangway: List[str]
    demandas_especiais: List[SpecialDemand] = field(default_factory=list)

    def vessel_map(self) -> Dict[str, FleetVessel]:
        return {item.nome: item for item in self.frota}

    def special_demand_map(self) -> Dict[str, SpecialDemand]:
        return {
            normalize_special_code(item.codigo): item
            for item in self.demandas_especiais
            if item.codigo.strip()
        }


@dataclass
class SolverRunResult:
    route_lines: List[str]
    distribution_text: str
    metrics: Dict[str, Any]
    warnings: List[str]


@dataclass
class VersionBundle:
    version: OperationVersion
    distribution_text: str = ""
    metrics: Optional[Dict[str, Any]] = None
    imported_csv_name: str = ""


@dataclass
class ComparisonSummary:
    operacao_id: str
    programacao_existe: bool
    cl_oficial_existe: bool
    delta_distancia_nm: float
    delta_total_tmib: int
    delta_total_m9: int
    delta_platforms_complete: int
    delta_service_minutes_complete: int
    changed_units_count: int
    priority_units_count: int
    priority_service_delta_minutes: int
    generated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
