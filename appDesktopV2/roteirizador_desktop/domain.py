from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


VERSION_PROGRAMACAO = "programacao"
VERSION_CL = "cl_oficial"
VERSION_TYPES = (VERSION_PROGRAMACAO, VERSION_CL)


def utc_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
class DemandItem:
    plataforma: str
    tmib: int = 0
    m9: int = 0
    m1: int = 0
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
class OperationVersion:
    versao: str
    usuario: str
    criado_em: str
    tipo_origem: str = "formulario"
    troca_turma: bool = False
    rendidos_m9: int = 0
    embarcacoes_disponiveis: List[AvailableBoat] = field(default_factory=list)
    demanda: List[DemandItem] = field(default_factory=list)

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
class ExtratoOriginConfig:
    codigo: str
    ativa: bool = True
    aceitar: List[str] = field(default_factory=list)
    descartar: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtratoOriginConfig":
        return cls(
            codigo=str(data.get("codigo", "")).strip().upper(),
            ativa=bool(data.get("ativa", True)),
            aceitar=[str(item).strip() for item in data.get("aceitar", []) if str(item).strip()],
            descartar=[str(item).strip() for item in data.get("descartar", []) if str(item).strip()],
        )


def default_extrato_origins() -> List[ExtratoOriginConfig]:
    return [
        ExtratoOriginConfig(
            codigo="M1",
            ativa=True,
            aceitar=["PCM-01 (D)", "PCM1 (D)", "M1 (D)", "M1 D"],
            descartar=["PCM-01 (N)", "PCM1 (N)", "M1 (N)", "M1 N"],
        ),
        ExtratoOriginConfig(
            codigo="M9",
            ativa=True,
            aceitar=["PCM-09", "PCM9", "M9"],
            descartar=[],
        ),
        ExtratoOriginConfig(
            codigo="TMIB",
            ativa=True,
            aceitar=["TMIB"],
            descartar=[],
        ),
    ]


@dataclass
class TideEvent:
    hora: str
    altura_m: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TideEvent":
        return cls(
            hora=str(data.get("hora", "")).strip(),
            altura_m=float(data.get("altura_m", 0.0)),
        )


@dataclass
class TideDay:
    data: str
    eventos: List[TideEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data": self.data,
            "eventos": [item.to_dict() for item in self.eventos],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TideDay":
        eventos = [
            TideEvent.from_dict(item)
            for item in data.get("eventos", [])
            if isinstance(item, dict)
        ]
        eventos.sort(key=lambda item: item.hora)
        return cls(
            data=str(data.get("data", "")).strip(),
            eventos=eventos,
        )


@dataclass
class TideTableConfig:
    ano_referencia: int = 0
    arquivo_origem: str = ""
    importada_em: str = ""
    dias: List[TideDay] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ano_referencia": self.ano_referencia,
            "arquivo_origem": self.arquivo_origem,
            "importada_em": self.importada_em,
            "dias": [item.to_dict() for item in self.dias],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TideTableConfig":
        dias = [
            TideDay.from_dict(item)
            for item in data.get("dias", [])
            if isinstance(item, dict)
        ]
        dias.sort(key=lambda item: item.data)
        return cls(
            ano_referencia=int(data.get("ano_referencia", 0) or 0),
            arquivo_origem=str(data.get("arquivo_origem", "")).strip(),
            importada_em=str(data.get("importada_em", "")).strip(),
            dias=dias,
        )

    def has_data(self) -> bool:
        return bool(self.dias)

    def available_dates(self) -> List[str]:
        return [item.data for item in self.dias if item.data]

    def get_day(self, day_iso: str) -> Optional[TideDay]:
        target = (day_iso or "").strip()
        for item in self.dias:
            if item.data == target:
                return item
        return None


@dataclass
class OperationalConfig:
    frota: List[FleetVessel]
    unidades: List[str]
    gangway: List[str]
    embarcacoes_conves: List[str]
    origens_extrato: List[ExtratoOriginConfig] = field(default_factory=default_extrato_origins)
    mare_limite_m: float = 0.6
    tabua_mare: TideTableConfig = field(default_factory=TideTableConfig)

    def vessel_map(self) -> Dict[str, FleetVessel]:
        return {item.nome: item for item in self.frota}


@dataclass
class SolverRunResult:
    route_lines: List[str]
    distribution_text: str
    metrics: Dict[str, Any]
    warnings: List[str]


@dataclass
class TideAdjustmentSuggestion:
    boat_name: str
    route_text: str
    trend: str
    critical_scope: str
    critical_platform: str
    current_departure: str
    suggested_departure: str
    current_arrival: str
    target_arrival: str
    current_height_m: float
    limit_height_m: float
    delta_minutes: int
    rationale: str = ""


@dataclass
class TideAlertAnalysis:
    operation_date: str = ""
    tide_source: str = ""
    limit_height_m: float = 0.0
    suggestions: List[TideAdjustmentSuggestion] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coordinator_text: str = ""

    def has_items(self) -> bool:
        return bool(self.suggestions or self.warnings)


@dataclass
class PickupDemand:
    plataforma: str
    origem: str
    quantidade: int
    prioridade: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PickupBoatState:
    nome: str
    localizacao: str = "TMIB"
    hora_disponivel: str = "00:00"
    disponivel: bool = True
    viagens_maximas: int = 2
    rota_fixa: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PickupPlanResult:
    plan_text: str
    warnings: List[str]
    demand_summary_text: str
    boat_states: List[PickupBoatState]


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
