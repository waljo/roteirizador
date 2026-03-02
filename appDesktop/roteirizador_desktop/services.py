from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import List, Optional

import solver

from .domain import (
    AppConfig,
    ComparisonSummary,
    FleetVessel,
    normalize_special_code,
    OperationalConfig,
    OperationMetadata,
    OperationVersion,
    SpecialDemand,
    SolverRunResult,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
    utc_now_iso,
)
from .runtime import resource_path
from .solver_integration import (
    export_programacao_planilha,
    import_demands_from_csv,
    parse_distribution_text,
    run_solver,
)
from .storage import LocalConfigStore, NetworkStorage
from .solver_integration import analyze_distribution


class AppService:
    def __init__(self):
        self.local_store = LocalConfigStore()

    def load_app_config(self) -> AppConfig:
        return self.local_store.load()

    def save_app_config(self, config: AppConfig) -> None:
        self.local_store.save(config)

    def network_storage(self, root: str) -> NetworkStorage:
        storage = NetworkStorage(root)
        storage.ensure_root()
        return storage

    def bootstrap_network_config(self, root: str) -> None:
        storage = self.network_storage(root)
        if not storage.config_path("distancias.json").exists():
            shutil.copy2(resource_path("resources/distplat.json"), storage.config_path("distancias.json"))
        if not storage.config_path("gangway.json").exists():
            shutil.copy2(resource_path("resources/gangway.json"), storage.config_path("gangway.json"))
        if not storage.config_path("frota.json").exists():
            speeds = solver.load_speeds(str(resource_path("resources/velocidades.txt")))
            vessel_names = sorted({
                "SURFER 1870",
                "SURFER 1871",
                "SURFER 1905",
                "SURFER 1930",
                "SURFER 1931",
                "AQUA HELIX",
            })
            fleet = []
            for name in vessel_names:
                fleet.append(
                    FleetVessel(
                        nome=name,
                        tipo="aqua" if solver.is_aqua_helix(name) else "surfer",
                        capacidade=solver.get_max_capacity(name),
                        velocidade=float(solver.get_speed(speeds, name)),
                        ativa=True,
                    ).to_dict()
                )
            storage.save_json_config("frota.json", {"version": 1, "embarcacoes": fleet})
        else:
            frota_payload = storage.load_json_config("frota.json")
            embarcacoes = frota_payload.get("embarcacoes", [])
            if embarcacoes and all(not bool(item.get("ativa", False)) for item in embarcacoes):
                for item in embarcacoes:
                    item["ativa"] = True
                storage.save_json_config(
                    "frota.json",
                    {"version": int(frota_payload.get("version", 1)), "embarcacoes": embarcacoes},
                )
        if not storage.config_path("unidades.json").exists():
            dist_data = json.loads(resource_path("resources/distplat.json").read_text(encoding="utf-8"))
            units = sorted(
                {
                    solver.short_plat(solver.norm_plat(key))
                    for key in dist_data.keys()
                    if solver.short_plat(solver.norm_plat(key)) != "TMIB"
                }
            )
            storage.save_json_config("unidades.json", {"version": 1, "unidades": units})
        if not storage.config_path("demandas_especiais.json").exists():
            storage.save_json_config("demandas_especiais.json", {"version": 1, "demandas_especiais": []})

    def load_operational_config(self, root: str) -> OperationalConfig:
        storage = self.network_storage(root)
        frota = storage.load_json_config("frota.json")
        unidades = storage.load_json_config("unidades.json")
        gangway = storage.load_json_config("gangway.json")
        especiais = storage.load_json_config("demandas_especiais.json")
        return OperationalConfig(
            frota=[FleetVessel(**item) for item in frota.get("embarcacoes", [])],
            unidades=unidades.get("unidades", []),
            gangway=gangway.get("plataformas_gangway", []),
            demandas_especiais=[
                SpecialDemand(**item) for item in especiais.get("demandas_especiais", [])
            ],
        )

    def save_operational_config(self, root: str, config: OperationalConfig) -> None:
        storage = self.network_storage(root)
        storage.save_json_config(
            "frota.json",
            {"version": 1, "embarcacoes": [item.to_dict() for item in config.frota]},
        )
        storage.save_json_config("unidades.json", {"version": 1, "unidades": config.unidades})
        storage.save_json_config(
            "gangway.json", {"version": 1, "plataformas_gangway": config.gangway}
        )
        storage.save_json_config(
            "demandas_especiais.json",
            {
                "version": 1,
                "demandas_especiais": [item.to_dict() for item in config.demandas_especiais],
            },
        )

    def create_operation(self, root: str, operation_date: str) -> OperationMetadata:
        storage = self.network_storage(root)
        existing = [item for item in storage.list_operations() if item.data_operacao == operation_date]
        seq = len(existing) + 1
        op_id = f"operacao_{operation_date.replace('-', '_')}_{seq:03d}"
        metadata = OperationMetadata(
            operacao_id=op_id,
            data_operacao=operation_date,
            criada_em=utc_now_iso(),
            status="em_andamento",
            display_name="",
        )
        storage.save_operation_metadata(metadata)
        return metadata

    def list_operations(self, root: str) -> List[OperationMetadata]:
        return self.network_storage(root).list_operations()

    def rename_operation(
        self,
        root: str,
        metadata: OperationMetadata,
        new_operation_id: str,
    ) -> OperationMetadata:
        return self.network_storage(root).rename_operation(metadata, new_operation_id)

    def delete_operation(self, root: str, metadata: OperationMetadata) -> None:
        self.network_storage(root).delete_operation(metadata)

    def save_version(
        self,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        imported_csv_path: Optional[Path] = None,
    ) -> OperationMetadata:
        self.network_storage(root).save_version(metadata, VersionBundle(version=version), imported_csv_path)
        updated = self._metadata_with_version_label(metadata, version)
        self.network_storage(root).save_operation_metadata(updated)
        return updated

    def load_version(
        self, root: str, metadata: OperationMetadata, version_name: str
    ) -> Optional[VersionBundle]:
        return self.network_storage(root).load_version(metadata, version_name)

    def load_comparison(self, root: str, metadata: OperationMetadata):
        return self.network_storage(root).load_comparison(metadata)

    def run_version(
        self,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        imported_csv_path: Optional[Path] = None,
    ) -> tuple[OperationMetadata, SolverRunResult]:
        op_config = self.load_operational_config(root)
        solver_version = self._filter_special_demands(version, op_config)
        result = run_solver(
            solver_version,
            op_config,
            str(self.network_storage(root).config_path("distancias.json")),
        )
        updated = self._metadata_with_version_label(metadata, version)
        self.network_storage(root).save_version(
            updated,
            VersionBundle(
                version=version,
                distribution_text=result.distribution_text,
                metrics=result.metrics,
            ),
            imported_csv_path,
        )
        self.network_storage(root).save_operation_metadata(updated)
        self._refresh_comparison(root, updated)
        return updated, result

    @staticmethod
    def _metadata_with_version_label(
        metadata: OperationMetadata,
        version: OperationVersion,
    ) -> OperationMetadata:
        user = (version.usuario or "usuario").strip()
        suffix = "DISTCL" if version.versao == VERSION_CL else "DISTPROG"
        return OperationMetadata(
            operacao_id=metadata.operacao_id,
            data_operacao=metadata.data_operacao,
            criada_em=metadata.criada_em,
            status=metadata.status,
            display_name=f"{user} - {suffix}",
        )

    def import_csv(self, csv_path: Path):
        return import_demands_from_csv(csv_path)

    def export_program_sheet(
        self,
        root: str,
        distribution_text: str,
        output_path: Path,
    ) -> Path:
        op_config = self.load_operational_config(root)
        storage = self.network_storage(root)
        return export_programacao_planilha(
            distribution_text=distribution_text,
            config=op_config,
            distances_path=str(storage.config_path("distancias.json")),
            output_path=output_path,
        )

    def export_cl_distribution_txt(
        self,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        distribution_text: str,
        output_path: Path,
    ) -> Path:
        content = self.build_cl_distribution_txt(root, metadata, version, distribution_text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def build_cl_distribution_txt(
        self,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        distribution_text: str,
    ) -> str:
        op_config = self.load_operational_config(root)
        regular_version = self._filter_special_demands(version, op_config)
        tmib_lines, m9_lines = self._build_tmib_m9_lines(root, distribution_text)
        m9_lines.extend(self._build_special_execution_lines(version, op_config))
        m9_lines = list(dict.fromkeys(m9_lines))
        lines = [
            "TABELA DE DEMANDA DE DISTRIBUICAO",
            "=" * 70,
            f"Operacao: {metadata.operacao_id}",
            f"Data da operacao: {metadata.data_operacao}",
            f"Versao: {version.versao}",
            f"Usuario: {version.usuario}",
            "",
            self._format_demand_table(regular_version),
        ]
        special_table = self.format_special_demands_table(version, op_config)
        if special_table:
            lines.extend(["", special_table])
        lines.extend(
            [
                "",
                "TEXTO DA DISTRIBUICAO",
                "=" * 70,
                distribution_text.strip(),
            ]
        )
        if tmib_lines:
            lines.extend(
                [
                    "",
                    "SAIDA TMIB",
                    "=" * 70,
                    *tmib_lines,
                ]
            )
        if m9_lines:
            lines.extend(
                [
                    "",
                    "SAIDA M9",
                    "=" * 70,
                    *m9_lines,
                ]
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _filter_special_demands(version: OperationVersion, config: OperationalConfig) -> OperationVersion:
        special_map = config.special_demand_map()
        filtered_demands = [
            item
            for item in version.demanda
            if not special_map.get(normalize_special_code(item.plataforma))
            or not special_map[normalize_special_code(item.plataforma)].excluir_do_solver
        ]
        return OperationVersion(
            versao=version.versao,
            usuario=version.usuario,
            criado_em=version.criado_em,
            tipo_origem=version.tipo_origem,
            troca_turma=version.troca_turma,
            rendidos_m9=version.rendidos_m9,
            embarcacoes_disponiveis=list(version.embarcacoes_disponiveis),
            demanda=filtered_demands,
            execucoes_especiais=list(version.execucoes_especiais),
        )

    @staticmethod
    def _format_demand_table(version: OperationVersion) -> str:
        header = f"{'PLATAFORMA':<14} {'M9':>6} {'TMIB':>6} {'PRIO':>6}"
        sep = "-" * len(header)
        rows = [header, sep]
        demands = [item for item in version.demanda if int(item.tmib) or int(item.m9)]
        for item in sorted(demands, key=lambda d: solver.short_plat(solver.norm_plat(d.plataforma))):
            rows.append(
                f"{solver.short_plat(solver.norm_plat(item.plataforma)):<14} "
                f"{int(item.m9):>6} {int(item.tmib):>6} {int(item.prioridade):>6}"
            )
        if len(rows) == 2:
            rows.append("(sem demanda informada)")
        return "\n".join(rows)

    def format_special_demands_table(self, version: OperationVersion, config: OperationalConfig) -> str:
        special_map = config.special_demand_map()
        rows = []
        row_map = {}
        header = f"{'CODIGO':<12} {'ORIGEM':<8} {'DESTINO':<8} {'HORARIO':<8} {'QTD':>6}"
        sep = "-" * len(header)
        for item in version.demanda:
            special = special_map.get(normalize_special_code(item.plataforma))
            if not special:
                continue
            qty = int(item.tmib) + int(item.m9)
            row_map[normalize_special_code(special.codigo)] = (
                f"{special.codigo:<12} {special.origem:<8} {special.destino:<8} {special.horario:<8} {qty:>6}"
            )
        for item in version.execucoes_especiais:
            special = special_map.get(normalize_special_code(item.codigo))
            if not special:
                continue
            key = normalize_special_code(special.codigo)
            if key in row_map:
                continue
            qty = self._infer_special_execution_qty(item.trajeto)
            row_map[key] = (
                f"{special.codigo:<12} {special.origem:<8} {special.destino:<8} {special.horario:<8} {qty:>6}"
            )
        rows = [row_map[key] for key in sorted(row_map)]
        if not rows:
            return ""
        return "\n".join(["DEMANDAS ESPECIAIS", "=" * 70, header, sep, *rows])

    @staticmethod
    def _infer_special_execution_qty(trajeto: str) -> int:
        first_part = trajeto.strip().split("/", 1)[0].strip()
        if not first_part:
            return 0
        qty = 0
        for token in first_part.split()[1:]:
            if token.startswith("+") and token[1:].isdigit():
                qty += int(token[1:])
        return qty

    def _build_tmib_m9_lines(self, root: str, distribution_text: str) -> tuple[List[str], List[str]]:
        op_config = self.load_operational_config(root)
        vessel_map = op_config.vessel_map()
        distances = solver.load_distances(str(self.network_storage(root).config_path("distancias.json")))
        tmib_lines: List[str] = []
        m9_lines: List[str] = []
        for boat_name, departure, route_str in parse_distribution_text(distribution_text):
            parts = [self._parse_route_part(part) for part in route_str.split("/") if part.strip()]
            if not parts:
                continue
            boat_label = self._short_boat_name(boat_name)
            tmib_route = self._build_tmib_route(parts)
            if tmib_route:
                tmib_lines.append(f"{boat_label} {departure} {tmib_route}")
            m9_route = self._build_m9_route(parts)
            if m9_route:
                departure_m9 = self._compute_m9_departure_time(
                    boat_name,
                    departure,
                    parts,
                    vessel_map,
                    distances,
                )
                if departure_m9:
                    m9_lines.append(f"{boat_label} {departure_m9} {m9_route}")
        return tmib_lines, m9_lines

    def _build_special_execution_lines(
        self,
        version: OperationVersion,
        config: OperationalConfig,
    ) -> List[str]:
        special_map = config.special_demand_map()
        lines: List[str] = []
        for item in version.execucoes_especiais:
            special = special_map.get(normalize_special_code(item.codigo))
            trajeto = item.trajeto.strip()
            horario = item.horario.strip() or (special.horario if special else "")
            embarcacao = item.embarcacao.strip()
            if not trajeto:
                continue
            boat_label = self._short_boat_name(embarcacao) if embarcacao else item.codigo
            lines.append(f"{boat_label} {horario} {trajeto}".strip())
        return lines

    @staticmethod
    def _short_boat_name(boat_name: str) -> str:
        tokens = boat_name.split()
        return tokens[-1] if tokens else boat_name

    @staticmethod
    def _parse_route_part(part: str) -> dict:
        tokens = part.strip().split()
        platform = tokens[0].strip()
        pickup = 0
        tmib_drop = 0
        m9_drop = 0
        for token in tokens[1:]:
            if token.startswith("+") and token[1:].isdigit():
                pickup += int(token[1:])
            elif token.startswith("(-") and token.endswith(")") and token[2:-1].isdigit():
                m9_drop += int(token[2:-1])
            elif token.startswith("-") and token[1:].isdigit():
                tmib_drop += int(token[1:])
        return {
            "platform": solver.short_plat(solver.norm_plat(platform)),
            "pickup": pickup,
            "tmib_drop": tmib_drop,
            "m9_drop": m9_drop,
        }

    @staticmethod
    def _build_tmib_route(parts: List[dict]) -> str:
        segments: List[str] = []
        for idx, part in enumerate(parts):
            platform = part["platform"]
            tokens = [platform]
            if idx == 0 and platform == "TMIB" and part["pickup"] > 0:
                tokens.append(f"+{part['pickup']}")
            if part["tmib_drop"] > 0:
                tokens.append(f"-{part['tmib_drop']}")
            if len(tokens) > 1 or (idx == 0 and platform == "TMIB"):
                segments.append(" ".join(tokens))
        return "/".join(segments)

    @staticmethod
    def _build_m9_route(parts: List[dict]) -> str:
        seen_m9 = False
        segments: List[str] = []
        for part in parts:
            platform = part["platform"]
            if platform == "M9":
                seen_m9 = True
                if part["pickup"] > 0:
                    segments.append(f"M9 +{part['pickup']}")
                continue
            if not seen_m9:
                continue
            if part["m9_drop"] > 0:
                segments.append(f"{platform} (-{part['m9_drop']})")
        return "/".join(segments)

    @staticmethod
    def _compute_m9_departure_time(
        boat_name: str,
        departure: str,
        parts: List[dict],
        vessel_map: dict,
        distances: dict,
    ) -> Optional[str]:
        if not parts:
            return None
        hour, minute = departure.split(":", 1)
        current_time = int(hour) * 60 + int(minute)
        current_pos = parts[0]["platform"]
        if current_pos == "M9":
            return departure
        vessel = vessel_map.get(boat_name)
        speed = float(vessel.velocidade) if vessel else 14.0
        is_aqua = bool(vessel and vessel.tipo.lower() == "aqua")
        for part in parts[1:]:
            platform = part["platform"]
            dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
            current_time += solver.travel_time_minutes(dist, speed)
            if is_aqua and platform != "TMIB":
                current_time += solver.AQUA_APPROACH_TIME
            op_minutes = int(part["pickup"]) + int(part["tmib_drop"]) + int(part["m9_drop"])
            current_time += op_minutes
            if platform == "M9":
                return f"{current_time // 60:02d}:{current_time % 60:02d}"
            current_pos = platform
        return None

    def _refresh_comparison(self, root: str, metadata: OperationMetadata) -> None:
        storage = self.network_storage(root)
        programacao = storage.load_version(metadata, VERSION_PROGRAMACAO)
        cl = storage.load_version(metadata, VERSION_CL)
        if not programacao or not cl or not programacao.metrics or not cl.metrics:
            return
        op_config = self.load_operational_config(root)
        filtered_programacao = self._filter_special_demands(programacao.version, op_config)
        filtered_cl = self._filter_special_demands(cl.version, op_config)
        distances_path = str(storage.config_path("distancias.json"))
        prog_analysis = analyze_distribution(
            filtered_programacao, programacao.distribution_text, op_config, distances_path
        )
        cl_analysis = analyze_distribution(filtered_cl, cl.distribution_text, op_config, distances_path)
        unit_rows, changed_units_count = self._build_unit_rows(
            filtered_programacao, filtered_cl, prog_analysis["units"], cl_analysis["units"]
        )
        boat_rows = self._build_boat_rows(
            programacao.version, cl.version, prog_analysis["boats"], cl_analysis["boats"]
        )
        priority_rows, priority_service_delta = self._build_priority_rows(unit_rows)
        summary = ComparisonSummary(
            operacao_id=metadata.operacao_id,
            programacao_existe=True,
            cl_oficial_existe=True,
            delta_distancia_nm=round(
                float(cl.metrics["total_distance_nm"]) - float(programacao.metrics["total_distance_nm"]), 3
            ),
            delta_total_tmib=int(cl.metrics["total_tmib"]) - int(programacao.metrics["total_tmib"]),
            delta_total_m9=int(cl.metrics["total_m9"]) - int(programacao.metrics["total_m9"]),
            delta_platforms_complete=int(cl.metrics["platforms_complete"])
            - int(programacao.metrics["platforms_complete"]),
            delta_service_minutes_complete=int(cl.metrics["service_minutes_complete"])
            - int(programacao.metrics["service_minutes_complete"]),
            changed_units_count=changed_units_count,
            priority_units_count=len(priority_rows),
            priority_service_delta_minutes=priority_service_delta,
            generated_at=utc_now_iso(),
        )
        details = self._build_comparison_details(
            metadata,
            summary,
            unit_rows,
            boat_rows,
            priority_rows,
            programacao,
            cl,
        )
        storage.save_comparison(metadata, summary, details)

    @staticmethod
    def _build_unit_rows(
        programacao: OperationVersion,
        cl: OperationVersion,
        prog_units: dict,
        cl_units: dict,
    ):
        prog_map = {item.plataforma: item for item in programacao.demanda if item.tmib or item.m9}
        cl_map = {item.plataforma: item for item in cl.demanda if item.tmib or item.m9}
        units = sorted(set(prog_map) | set(cl_map))
        rows = []
        changed = 0
        for unit in units:
            prog = prog_map.get(unit)
            clv = cl_map.get(unit)
            prog_analysis = prog_units.get(unit, {})
            cl_analysis = cl_units.get(unit, {})
            row = {
                "plataforma": unit,
                "prog_tmib": int(prog.tmib) if prog else 0,
                "cl_tmib": int(clv.tmib) if clv else 0,
                "delta_tmib": (int(clv.tmib) if clv else 0) - (int(prog.tmib) if prog else 0),
                "prog_m9": int(prog.m9) if prog else 0,
                "cl_m9": int(clv.m9) if clv else 0,
                "delta_m9": (int(clv.m9) if clv else 0) - (int(prog.m9) if prog else 0),
                "prog_prio": int(prog.prioridade) if prog else 0,
                "cl_prio": int(clv.prioridade) if clv else 0,
                "prog_service": prog_analysis.get("service_minutes"),
                "cl_service": cl_analysis.get("service_minutes"),
                "delta_service": (cl_analysis.get("service_minutes") or 0)
                - (prog_analysis.get("service_minutes") or 0),
            }
            row["changed"] = any(
                [
                    row["delta_tmib"] != 0,
                    row["delta_m9"] != 0,
                    row["prog_prio"] != row["cl_prio"],
                    row["delta_service"] != 0,
                ]
            )
            if row["changed"]:
                changed += 1
            rows.append(row)
        return rows, changed

    @staticmethod
    def _build_boat_rows(
        programacao: OperationVersion,
        cl: OperationVersion,
        prog_boats: dict,
        cl_boats: dict,
    ):
        prog_available = {item.nome: item for item in programacao.embarcacoes_disponiveis if item.disponivel}
        cl_available = {item.nome: item for item in cl.embarcacoes_disponiveis if item.disponivel}
        boats = sorted(set(prog_available) | set(cl_available) | set(prog_boats) | set(cl_boats))
        rows = []
        for boat in boats:
            prog = prog_boats.get(boat, {})
            clv = cl_boats.get(boat, {})
            rows.append(
                {
                    "embarcacao": boat,
                    "prog_ativa": boat in prog_available,
                    "cl_ativa": boat in cl_available,
                    "prog_dist": prog.get("distance_nm", 0),
                    "cl_dist": clv.get("distance_nm", 0),
                    "delta_dist": round(float(clv.get("distance_nm", 0)) - float(prog.get("distance_nm", 0)), 3),
                    "prog_route": prog.get("route", ""),
                    "cl_route": clv.get("route", ""),
                }
            )
        return rows

    @staticmethod
    def _build_priority_rows(unit_rows: list):
        rows = []
        total_service_delta = 0
        for row in unit_rows:
            if row["prog_prio"] > 0 or row["cl_prio"] > 0:
                rows.append(row)
                total_service_delta += row["delta_service"]
        rows.sort(key=lambda item: (item["cl_prio"] or item["prog_prio"] or 999, item["plataforma"]))
        return rows, total_service_delta

    @staticmethod
    def _fmt_service(value):
        if value is None:
            return "-"
        return str(value)

    def _build_comparison_details(
        self,
        metadata: OperationMetadata,
        summary: ComparisonSummary,
        unit_rows: list,
        boat_rows: list,
        priority_rows: list,
        programacao: VersionBundle,
        cl: VersionBundle,
    ) -> str:
        lines = []
        lines.append(f"OPERACAO: {metadata.operacao_id}")
        lines.append("")
        lines.append("RESUMO EXECUTIVO")
        lines.append(f"- Distancia total Programacao: {programacao.metrics['total_distance_nm']} NM")
        lines.append(f"- Distancia total CL Oficial: {cl.metrics['total_distance_nm']} NM")
        lines.append(f"- Delta distancia (CL - Programacao): {summary.delta_distancia_nm} NM")
        lines.append(f"- Delta TMIB: {summary.delta_total_tmib}")
        lines.append(f"- Delta M9: {summary.delta_total_m9}")
        lines.append(f"- Delta plataformas completas: {summary.delta_platforms_complete}")
        lines.append(f"- Delta service minutes complete: {summary.delta_service_minutes_complete}")
        lines.append(f"- Unidades alteradas: {summary.changed_units_count}")
        lines.append("")
        lines.append("IMPACTO EM PRIORIDADES")
        if not priority_rows:
            lines.append("- Nenhuma unidade com prioridade definida nas duas versoes.")
        else:
            lines.append(f"- Unidades prioritarias avaliadas: {summary.priority_units_count}")
            lines.append(
                f"- Delta agregado de service minutes nas prioridades (CL - Programacao): "
                f"{summary.priority_service_delta_minutes}"
            )
            for row in priority_rows:
                lines.append(
                    f"- {row['plataforma']}: prio {row['prog_prio']} -> {row['cl_prio']}, "
                    f"TMIB {row['prog_tmib']} -> {row['cl_tmib']}, "
                    f"M9 {row['prog_m9']} -> {row['cl_m9']}, "
                    f"service {self._fmt_service(row['prog_service'])} -> {self._fmt_service(row['cl_service'])}, "
                    f"delta {row['delta_service']}"
                )
        lines.append("")
        lines.append("DIFERENCAS POR UNIDADE")
        for row in unit_rows:
            marker = "*" if row["changed"] else "="
            lines.append(
                f"{marker} {row['plataforma']}: "
                f"TMIB {row['prog_tmib']} -> {row['cl_tmib']} ({row['delta_tmib']:+}), "
                f"M9 {row['prog_m9']} -> {row['cl_m9']} ({row['delta_m9']:+}), "
                f"Prio {row['prog_prio']} -> {row['cl_prio']}, "
                f"Service {self._fmt_service(row['prog_service'])} -> {self._fmt_service(row['cl_service'])} "
                f"({row['delta_service']:+})"
            )
        lines.append("")
        lines.append("DIFERENCAS POR EMBARCACAO")
        for row in boat_rows:
            lines.append(
                f"- {row['embarcacao']}: ativa Prog={row['prog_ativa']} CL={row['cl_ativa']}, "
                f"dist {row['prog_dist']} -> {row['cl_dist']} ({row['delta_dist']:+})"
            )
            if row["prog_route"] or row["cl_route"]:
                lines.append(f"  Prog: {row['prog_route'] or '-'}")
                lines.append(f"  CL  : {row['cl_route'] or '-'}")
        return "\n".join(lines)


def default_operation_version(version_name: str, user_name: str) -> OperationVersion:
    return OperationVersion(versao=version_name, usuario=user_name, criado_em=utc_now_iso())


def today_iso() -> str:
    return date.today().isoformat()
