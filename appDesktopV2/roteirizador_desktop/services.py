from __future__ import annotations

import json
import math
import shutil
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

import solver_v2 as solver

from .domain import (
    AppConfig,
    ComparisonSummary,
    ExtratoOriginConfig,
    FleetVessel,
    OperationalConfig,
    OperationMetadata,
    OperationVersion,
    PickupBoatState,
    PickupDemand,
    PickupPlanResult,
    SolverRunResult,
    TideAlertAnalysis,
    TideTableConfig,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
    VERSION_TYPES,
    default_extrato_origins,
    utc_now_iso,
)
from .route_syntax import parse_route_part
from .pickup_planner import (
    build_pickup_demands,
    format_pickup_demand_summary,
    infer_importable_pickup_routes,
    infer_pickup_boat_states,
)
from .pickup_planner_v2 import plan_pickup as plan_pickup_v2
from .offshore_pd import (
    AliasResolver,
    Boat,
    Request,
    SolverParameters,
    check_solution,
    format_operational_text,
    load_distance_matrix,
    solution_to_json_dict,
    solve_pickup_delivery,
)
from .runtime import resource_path
from .solver_integration import (
    analyze_tide_alerts,
    export_programacao_planilha,
    import_demands_from_csv,
    import_demands_from_extrato_pdf,
    parse_distribution_text,
    run_solver,
    summarize_distribution_for_compare,
)
from .storage import LocalConfigStore, NetworkStorage
from .solver_integration import analyze_distribution
from .tide_table import load_tide_table_from_pdf

TXT_VALID_ORIGINS = ("TMIB", "M9", "M1")


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
                        tempo_aproximacao_min=25.0 if solver.is_aqua_helix(name) else 0.0,
                        tempo_travessia_pax_min=1.0,
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
        if not storage.config_path("embarcacoes_conves.json").exists():
            storage.save_json_config(
                "embarcacoes_conves.json",
                {
                    "version": 1,
                    "embarcacoes_conves": [
                        "BARU TAURUS",
                        "C-ITACURUCA",
                        "COSTA OCEANICA",
                    ],
                },
            )
        if not storage.config_path("origens_extrato.json").exists():
            storage.save_json_config(
                "origens_extrato.json",
                {
                    "version": 1,
                    "origens": [item.to_dict() for item in default_extrato_origins()],
                },
            )
        if not storage.config_path("rotas_fixas_salvas.json").exists():
            storage.save_json_config(
                "rotas_fixas_salvas.json",
                {"version": 1, "rotas": []},
            )
        if not storage.config_path("tabua_mare.json").exists():
            storage.save_json_config(
                "tabua_mare.json",
                {
                    "version": 1,
                    "altura_limite_m": 0.6,
                    "tabua": TideTableConfig().to_dict(),
                },
            )

    def load_saved_routes(self, root: str) -> List[dict]:
        storage = self.network_storage(root)
        data = storage.load_json_config("rotas_fixas_salvas.json")
        return data.get("rotas", [])

    def save_saved_routes(self, root: str, routes: List[dict]) -> None:
        storage = self.network_storage(root)
        storage.save_json_config(
            "rotas_fixas_salvas.json",
            {"version": 1, "rotas": routes},
        )

    def add_saved_route(self, root: str, nome: str, rota: str) -> List[dict]:
        routes = self.load_saved_routes(root)
        routes.append({"nome": nome, "rota": rota})
        self.save_saved_routes(root, routes)
        return routes

    def update_saved_route(self, root: str, index: int, nome: str, rota: str) -> List[dict]:
        routes = self.load_saved_routes(root)
        if 0 <= index < len(routes):
            routes[index] = {"nome": nome, "rota": rota}
            self.save_saved_routes(root, routes)
        return routes

    def delete_saved_route(self, root: str, index: int) -> List[dict]:
        routes = self.load_saved_routes(root)
        if 0 <= index < len(routes):
            routes.pop(index)
            self.save_saved_routes(root, routes)
        return routes

    def load_operational_config(self, root: str) -> OperationalConfig:
        storage = self.network_storage(root)
        frota = storage.load_json_config("frota.json")
        unidades = storage.load_json_config("unidades.json")
        gangway = storage.load_json_config("gangway.json")
        conves = storage.load_json_config("embarcacoes_conves.json")
        origens_extrato_payload = storage.load_json_config("origens_extrato.json")
        tabua_mare_payload = storage.load_json_config("tabua_mare.json")
        origens_extrato = [
            ExtratoOriginConfig.from_dict(item)
            for item in origens_extrato_payload.get("origens", [])
        ] or default_extrato_origins()
        return OperationalConfig(
            frota=[FleetVessel.from_dict(item) for item in frota.get("embarcacoes", [])],
            unidades=unidades.get("unidades", []),
            gangway=gangway.get("plataformas_gangway", []),
            embarcacoes_conves=conves.get(
                "embarcacoes_conves",
                ["BARU TAURUS", "C-ITACURUCA", "COSTA OCEANICA"],
            ),
            origens_extrato=origens_extrato,
            mare_limite_m=float(tabua_mare_payload.get("altura_limite_m", 0.6) or 0.6),
            tabua_mare=TideTableConfig.from_dict(tabua_mare_payload.get("tabua", {})),
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
            "embarcacoes_conves.json",
            {"version": 1, "embarcacoes_conves": config.embarcacoes_conves},
        )
        storage.save_json_config(
            "origens_extrato.json",
            {"version": 1, "origens": [item.to_dict() for item in config.origens_extrato]},
        )
        storage.save_json_config(
            "tabua_mare.json",
            {
                "version": 1,
                "altura_limite_m": float(config.mare_limite_m or 0.0),
                "tabua": config.tabua_mare.to_dict(),
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
        new_display_name: str,
    ) -> OperationMetadata:
        updated = OperationMetadata(
            operacao_id=metadata.operacao_id,
            data_operacao=metadata.data_operacao,
            criada_em=metadata.criada_em,
            status=metadata.status,
            display_name=new_display_name.strip(),
        )
        return self.network_storage(root).update_operation_metadata(updated)

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
        updated = self._metadata_with_operation_label(root, metadata)
        self.network_storage(root).save_operation_metadata(updated)
        return updated

    def load_version(
        self, root: str, metadata: OperationMetadata, version_name: str
    ) -> Optional[VersionBundle]:
        return self.network_storage(root).load_version(metadata, version_name)

    def load_comparison(self, root: str, metadata: OperationMetadata):
        return self.network_storage(root).load_comparison(metadata)

    def load_pickup_context(
        self,
        root: str,
        metadata: OperationMetadata,
        version_name: str,
    ) -> tuple[Optional[VersionBundle], List[PickupBoatState], List[PickupDemand]]:
        if version_name not in VERSION_TYPES:
            raise ValueError("Versao invalida para recolhimento.")
        bundle = self.load_version(root, metadata, version_name)
        if bundle is None:
            return None, [], []
        op_config = self.load_operational_config(root)
        boat_states = infer_pickup_boat_states(
            bundle.version,
            bundle.distribution_text,
            op_config,
            str(self.network_storage(root).config_path("distancias.json")),
            initial_only=True,
        )
        for item in boat_states:
            item.rota_fixa = ""
        cl_bundle = self.load_version(root, metadata, VERSION_CL)
        if cl_bundle is not None:
            state_map = {item.nome: item for item in boat_states}
            for nome, (departure, route_text) in infer_importable_pickup_routes(
                cl_bundle.distribution_text
            ).items():
                current = state_map.get(nome)
                if current is None:
                    current = PickupBoatState(
                        nome=nome,
                        localizacao="TMIB",
                        hora_disponivel=departure or "00:00",
                        disponivel=True,
                        viagens_maximas=2,
                        rota_fixa=(route_text or "").strip(),
                    )
                    boat_states.append(current)
                    state_map[nome] = current
                else:
                    current.rota_fixa = (route_text or "").strip()
                    if departure:
                        current.hora_disponivel = departure
        boat_states = sorted(boat_states, key=lambda item: (item.hora_disponivel, item.nome))
        return bundle, boat_states, build_pickup_demands(bundle.version, bundle.distribution_text)

    def plan_pickup(
        self,
        root: str,
        metadata: OperationMetadata,
        version_name: str,
        boat_states: List[PickupBoatState],
        custom_demands: Optional[List[PickupDemand]],
        surfer_cutoff_hhmm: str,
        execution_mode: str = "plan",
        now_hhmm: str = "00:00",
        include_late_fixed_routes: Optional[bool] = None,
        pickup_engine: str = "legacy_v2",
    ) -> PickupPlanResult:
        if version_name not in VERSION_TYPES:
            raise ValueError("Versao invalida para recolhimento.")
        bundle = self.load_version(root, metadata, version_name)
        if bundle is None:
            raise ValueError("Versao selecionada ainda nao foi salva.")
        if not bundle.distribution_text.strip():
            raise ValueError("Gere ou carregue uma distribuicao antes de planejar o recolhimento.")
        op_config = self.load_operational_config(root)
        distances_path = str(self.network_storage(root).config_path("distancias.json"))
        if pickup_engine == "pd_v1":
            return self._plan_pickup_pd_v1(
                op_config=op_config,
                boat_states=boat_states,
                custom_demands=custom_demands,
                distances_path=distances_path,
                execution_mode=execution_mode,
                surfer_cutoff_hhmm=surfer_cutoff_hhmm,
            )

        return plan_pickup_v2(
            bundle.version,
            bundle.distribution_text,
            op_config,
            distances_path,
            boat_states,
            custom_demands=custom_demands,
            surfer_cutoff_hhmm=surfer_cutoff_hhmm,
            execution_mode=execution_mode,
            now_hhmm=now_hhmm,
            include_late_fixed_routes=include_late_fixed_routes,
        )

    def _plan_pickup_pd_v1(
        self,
        op_config: OperationalConfig,
        boat_states: List[PickupBoatState],
        custom_demands: Optional[List[PickupDemand]],
        distances_path: str,
        execution_mode: str,
        surfer_cutoff_hhmm: str,
    ) -> PickupPlanResult:
        resolver = AliasResolver()
        distances = load_distance_matrix(distances_path, resolver=resolver)
        vessel_map = op_config.vessel_map()

        source_demands: List[PickupDemand] = []
        for item in custom_demands or []:
            if int(item.quantidade) <= 0:
                continue
            if not (item.plataforma or "").strip() or not (item.origem or "").strip():
                continue
            source_demands.append(
                PickupDemand(
                    plataforma=item.plataforma.strip(),
                    origem=item.origem.strip(),
                    quantidade=int(item.quantidade),
                    prioridade=max(0, int(item.prioridade)),
                )
            )
        if not source_demands:
            raise ValueError("Demanda de recolhimento vazia para o motor PD V1.")

        requests: List[Request] = []
        for idx, item in enumerate(source_demands, start=1):
            requests.append(
                Request(
                    request_id=f"REQ-{idx:04d}",
                    pickup=item.plataforma,
                    delivery=item.origem,
                    pax=int(item.quantidade),
                    priority=max(0, int(item.prioridade)),
                )
            )

        boats: List[Boat] = []
        warnings: List[str] = []
        has_fixed_routes = False
        for state in boat_states:
            if not state.disponivel or int(state.viagens_maximas) <= 0:
                continue
            vessel = vessel_map.get(state.nome)
            if vessel is None:
                continue
            if (state.rota_fixa or "").strip():
                has_fixed_routes = True
            boats.append(
                Boat(
                    name=state.nome,
                    start=(state.localizacao or "TMIB"),
                    capacity=int(vessel.capacidade),
                    end="TMIB",
                )
            )
        if not boats:
            raise ValueError("Nao ha embarcacoes disponiveis para o motor PD V1.")

        if has_fixed_routes:
            warnings.append("Motor PD V1 ignora rotas fixas nesta versao de teste.")
        if execution_mode != "plan":
            warnings.append(
                f"Motor PD V1 executado em modo '{execution_mode}', mas sem logica especifica de janela/fixa."
            )

        solution = solve_pickup_delivery(
            requests=requests,
            boats=boats,
            distances=distances,
            params=SolverParameters(
                engine="python",
                time_limit_sec=20,
                max_lns_iterations=30,
                allow_split_requests=True,
                allow_unserved=False,
            ),
            resolver=resolver,
        )
        errors = check_solution(solution, requests, boats, distances, resolver=resolver)
        if errors:
            raise ValueError("Solucao PD V1 invalida:\n" + "\n".join(errors))

        plan_text = (
            "PLANO DE RECOLHIMENTO\n"
            + "=" * 70
            + "\n"
            + f"Motor recolhimento: pd_v1\n"
            + f"Horario de chegada ao TMIB (referencia): {surfer_cutoff_hhmm}\n\n"
            + format_operational_text(solution, resolver=resolver).strip()
            + (
                "\n\nAVISOS\n"
                + "-" * 70
                + "\n"
                + "\n".join(f"- {item}" for item in warnings)
                if warnings
                else ""
            )
            + "\n\nJSON RESUMO\n"
            + "-" * 70
            + "\n"
            + json.dumps(solution_to_json_dict(solution, resolver=resolver), ensure_ascii=False, indent=2)
            + "\n"
        )
        return PickupPlanResult(
            plan_text=plan_text,
            warnings=warnings,
            demand_summary_text=format_pickup_demand_summary(source_demands),
            boat_states=boat_states,
        )

    def run_version(
        self,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        imported_csv_path: Optional[Path] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[OperationMetadata, SolverRunResult]:
        op_config = self.load_operational_config(root)
        result = run_solver(
            version,
            op_config,
            str(self.network_storage(root).config_path("distancias.json")),
            progress_callback=progress_callback,
        )
        self.network_storage(root).save_version(
            metadata,
            VersionBundle(
                version=version,
                distribution_text=result.distribution_text,
                metrics=result.metrics,
            ),
            imported_csv_path,
        )
        updated = self._metadata_with_operation_label(root, metadata)
        self.network_storage(root).save_operation_metadata(updated)
        self._refresh_comparison(root, updated)
        return updated, result

    def _metadata_with_operation_label(
        self,
        root: str,
        metadata: OperationMetadata,
    ) -> OperationMetadata:
        storage = self.network_storage(root)
        programacao_name = ""
        cl_name = ""
        programacao = storage.load_version(metadata, VERSION_PROGRAMACAO)
        cl = storage.load_version(metadata, VERSION_CL)
        if programacao and programacao.version.usuario.strip():
            programacao_name = programacao.version.usuario.strip()
        if cl and cl.version.usuario.strip():
            cl_name = cl.version.usuario.strip()
        display_name = " | ".join(part for part in [programacao_name, cl_name] if part)
        return OperationMetadata(
            operacao_id=metadata.operacao_id,
            data_operacao=metadata.data_operacao,
            criada_em=metadata.criada_em,
            status=metadata.status,
            display_name=display_name,
        )

    def import_csv(self, csv_path: Path):
        return import_demands_from_csv(csv_path)

    def import_extrato_pdf(self, root: str, pdf_path: Path):
        op_config = self.load_operational_config(root)
        return import_demands_from_extrato_pdf(pdf_path, op_config.origens_extrato)

    def import_tide_table_pdf(self, pdf_path: Path) -> TideTableConfig:
        return load_tide_table_from_pdf(pdf_path)

    def analyze_tide_alerts(
        self,
        root: str,
        operation_date: str,
        distribution_text: str,
    ) -> TideAlertAnalysis:
        op_config = self.load_operational_config(root)
        storage = self.network_storage(root)
        return analyze_tide_alerts(
            operation_date=operation_date,
            distribution_text=distribution_text,
            config=op_config,
            distances_path=str(storage.config_path("distancias.json")),
        )

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
            embarcacoes_conves=op_config.embarcacoes_conves,
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
        origin_lines = self._build_origin_departure_lines(root, distribution_text)
        lines = [
            "TABELA DE DEMANDA DE DISTRIBUICAO",
            "=" * 70,
            f"Operacao: {metadata.operacao_id}",
            f"Data da operacao: {metadata.data_operacao}",
            f"Versao: {version.versao}",
            f"Usuario: {version.usuario}",
            "",
            self._format_demand_table(version),
        ]
        lines.extend(
            [
                "",
                "TEXTO DA DISTRIBUICAO",
                "=" * 70,
                distribution_text.strip(),
            ]
        )
        for origin in TXT_VALID_ORIGINS:
            section_lines = origin_lines.get(origin, [])
            if not section_lines:
                continue
            lines.extend(
                [
                    "",
                    f"SAIDA {origin}",
                    "=" * 70,
                    *section_lines,
                ]
            )
        lines.append("")
        return "\n".join(lines)

    def compare_automatic_vs_manual_routes(
        self,
        root: str,
        automatic_distribution_text: str,
        manual_distribution_text: str,
    ) -> Dict[str, Any]:
        op_config = self.load_operational_config(root)
        storage = self.network_storage(root)
        distances_path = str(storage.config_path("distancias.json"))
        normalized_manual_text = self._normalize_manual_distribution_for_compare(
            automatic_distribution_text,
            manual_distribution_text,
        )
        automatic = summarize_distribution_for_compare(
            automatic_distribution_text,
            op_config,
            distances_path,
        )
        manual = summarize_distribution_for_compare(
            normalized_manual_text,
            op_config,
            distances_path,
        )
        if automatic["demand"] != manual["demand"]:
            diff_lines: list[str] = []
            all_plats = sorted(set(automatic["demand"]) | set(manual["demand"]))
            for plat in all_plats:
                auto_d = automatic["demand"].get(plat, {"tmib": 0, "m9": 0})
                man_d = manual["demand"].get(plat, {"tmib": 0, "m9": 0})
                if auto_d == man_d:
                    continue
                parts: list[str] = []
                if auto_d["tmib"] != man_d["tmib"]:
                    parts.append(f"TMIB auto={auto_d['tmib']} manual={man_d['tmib']}")
                if auto_d["m9"] != man_d["m9"]:
                    parts.append(f"M9 auto={auto_d['m9']} manual={man_d['m9']}")
                diff_lines.append(f"  {plat}: {', '.join(parts)}")
            details = "\n".join(diff_lines)
            raise ValueError(
                "Para rodar a comparacao as demandas de pax devem ser iguais.\n\n"
                f"Diferencas encontradas:\n{details}"
            )

        platforms = sorted(set(automatic["arrivals"]) | set(manual["arrivals"]))
        return {
            "automatic_total_distance_nm": automatic["total_distance_nm"],
            "manual_total_distance_nm": manual["total_distance_nm"],
            "platform_rows": [
                {
                    "platform": platform,
                    "automatic_arrival": automatic["arrivals"].get(platform, "-"),
                    "manual_arrival": manual["arrivals"].get(platform, "-"),
                }
                for platform in platforms
            ],
        }

    @staticmethod
    def _normalize_manual_distribution_for_compare(
        automatic_distribution_text: str,
        manual_distribution_text: str,
    ) -> str:
        auto_routes = parse_distribution_text(automatic_distribution_text)
        manual_routes = parse_distribution_text(manual_distribution_text)
        if manual_routes:
            return manual_distribution_text
        if not auto_routes:
            return manual_distribution_text

        departure_by_boat = {boat_name.strip().upper(): departure for boat_name, departure, _ in auto_routes}
        boat_names = sorted(departure_by_boat.keys(), key=len, reverse=True)
        normalized_lines: List[str] = []
        changed = False
        for raw_line in manual_distribution_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            upper_line = line.upper()
            matched_boat = None
            for boat_name in boat_names:
                prefix = f"{boat_name} "
                if upper_line == boat_name or upper_line.startswith(prefix):
                    matched_boat = boat_name
                    break
            if not matched_boat:
                normalized_lines.append(line)
                continue
            departure = departure_by_boat.get(matched_boat)
            if not departure:
                normalized_lines.append(line)
                continue
            route_part = line[len(matched_boat):].strip()
            if not route_part:
                normalized_lines.append(line)
                continue
            normalized_lines.append(f"{matched_boat}  {departure}  {route_part}")
            changed = True
        if not changed:
            return manual_distribution_text
        return "\n".join(normalized_lines) + "\n"

    @staticmethod
    def _format_demand_table(version: OperationVersion) -> str:
        extra_origins: List[str] = []
        if any(int(getattr(item, "m1", 0) or 0) for item in version.demanda):
            extra_origins.append("M1")
        for item in version.demanda:
            for origin, qty in getattr(item, "origens_extras", {}).items():
                origin_up = (origin or "").strip().upper()
                if origin_up and origin_up not in {"TMIB", "M9", "M1"} and int(qty) != 0:
                    if origin_up not in extra_origins:
                        extra_origins.append(origin_up)
        header = f"{'PLATAFORMA':<14} {'TMIB':>6} {'M9':>6}"
        for origin in extra_origins:
            header += f" {origin:>6}"
        header += f" {'PRIO':>6}"
        sep = "-" * len(header)
        rows = [header, sep]
        demands = [
            item
            for item in version.demanda
            if int(item.tmib)
            or int(item.m9)
            or int(getattr(item, "m1", 0))
            or any(int(qty) != 0 for qty in getattr(item, "origens_extras", {}).values())
        ]
        for item in sorted(demands, key=lambda d: solver.short_plat(solver.norm_plat(d.plataforma))):
            row_text = (
                f"{solver.short_plat(solver.norm_plat(item.plataforma)):<14} "
                f"{int(item.tmib):>6} {int(item.m9):>6}"
            )
            for origin in extra_origins:
                row_text += f" {int(item.quantidade_origem(origin)):>6}"
            row_text += f" {int(item.prioridade):>6}"
            rows.append(row_text)
        if len(rows) == 2:
            rows.append("(sem demanda informada)")
        return "\n".join(rows)

    def _build_origin_departure_lines(self, root: str, distribution_text: str) -> Dict[str, List[str]]:
        op_config = self.load_operational_config(root)
        vessel_map = op_config.vessel_map()
        distances = solver.load_distances(str(self.network_storage(root).config_path("distancias.json")))
        origin_lines = {origin: [] for origin in TXT_VALID_ORIGINS}
        for boat_name, departure, route_str in parse_distribution_text(distribution_text):
            parts = [self._parse_route_part(part) for part in route_str.split("/") if part.strip()]
            if not parts:
                continue
            boat_label = self._short_boat_name(boat_name)
            for origin in TXT_VALID_ORIGINS:
                for origin_departure, origin_route in self._build_origin_departures(
                    origin,
                    boat_name,
                    departure,
                    parts,
                    vessel_map,
                    distances,
                ):
                    origin_lines[origin].append(f"{boat_label} {origin_departure} {origin_route}")
        return {origin: lines for origin, lines in origin_lines.items() if lines}

    @staticmethod
    def _short_boat_name(boat_name: str) -> str:
        tokens = boat_name.split()
        return tokens[-1] if tokens else boat_name

    @staticmethod
    def _parse_route_part(part: str) -> dict:
        parsed = parse_route_part(part)
        if parsed is None:
            return {
                "platform": "",
                "pickup": 0,
                "tmib_drop": 0,
                "m9_drop": 0,
                "total_drop": 0,
                "drops_by_origin": {},
            }
        drops_by_origin = {
            solver.short_plat(solver.norm_plat(origin)): int(qty)
            for origin, qty in parsed.drops_by_origin.items()
            if int(qty) > 0
        }
        return {
            "platform": solver.short_plat(solver.norm_plat(parsed.platform)),
            "pickup": int(parsed.pickup_qty),
            "tmib_drop": int(parsed.tmib_drop),
            "m9_drop": int(parsed.m9_drop),
            "total_drop": int(parsed.total_drop),
            "drops_by_origin": drops_by_origin,
        }

    @staticmethod
    def _format_origin_drop(origin: str, qty: int) -> str:
        if origin == "TMIB":
            return f"-{int(qty)}"
        return f"-{origin}:{int(qty)}"

    @staticmethod
    def _build_origin_route(origin: str, parts: List[dict], start_index: int) -> str:
        segments: List[str] = []
        for relative_idx, part in enumerate(parts[start_index:]):
            platform = part["platform"]
            if relative_idx > 0 and platform == origin and int(part["pickup"]) > 0:
                break
            tokens = [platform]
            if relative_idx == 0 and platform == origin and part["pickup"] > 0:
                tokens.append(f"+{part['pickup']}")
            drop_qty = int(part.get("drops_by_origin", {}).get(origin, 0))
            if drop_qty > 0:
                tokens.append(AppService._format_origin_drop(origin, drop_qty))
            if len(tokens) > 1 or (relative_idx == 0 and platform == origin):
                segments.append(" ".join(tokens))
        return "/".join(segments)

    def _build_origin_departures(
        self,
        origin: str,
        boat_name: str,
        departure: str,
        parts: List[dict],
        vessel_map: dict,
        distances: dict,
    ) -> List[tuple[str, str]]:
        origin_departures: List[tuple[str, str]] = []
        if not parts:
            return origin_departures

        for idx, part in enumerate(parts):
            if part["platform"] != origin or int(part["pickup"]) <= 0:
                continue
            route = self._build_origin_route(origin, parts, idx)
            if not route:
                continue
            departure_origin = self._compute_part_departure_time(
                boat_name,
                departure,
                parts,
                idx,
                vessel_map,
                distances,
            )
            if departure_origin:
                origin_departures.append((departure_origin, route))

        return origin_departures

    @staticmethod
    def _compute_part_departure_time(
        boat_name: str,
        departure: str,
        parts: List[dict],
        target_index: int,
        vessel_map: dict,
        distances: dict,
        require_pickup: bool = False,
    ) -> Optional[str]:
        if not parts:
            return None
        if target_index < 0 or target_index >= len(parts):
            return None
        hour, minute = departure.split(":", 1)
        current_time = int(hour) * 60 + int(minute)
        current_pos = parts[0]["platform"]
        target_part = parts[target_index]
        if current_pos == target_part["platform"] and target_index == 0:
            if require_pickup and target_part["pickup"] <= 0:
                return None
            return departure
        vessel = vessel_map.get(boat_name)
        speed = float(vessel.velocidade) if vessel else 14.0
        approach_minutes = float(vessel.tempo_aproximacao_min) if vessel else 0.0
        minutes_per_pax = float(vessel.tempo_travessia_pax_min) if vessel else 1.0
        for idx, part in enumerate(parts[1:], start=1):
            platform = part["platform"]
            dist = solver.get_dist(distances, solver.norm_plat(current_pos), solver.norm_plat(platform))
            current_time += solver.travel_time_minutes(dist, speed)
            if approach_minutes > 0 and platform != "TMIB":
                current_time += int(math.ceil(approach_minutes))
            op_minutes = int(part["pickup"]) + int(part.get("total_drop", 0))
            current_time += int(math.ceil(op_minutes * minutes_per_pax))
            if idx == target_index:
                if require_pickup and int(part["pickup"]) <= 0:
                    return None
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
        distances_path = str(storage.config_path("distancias.json"))
        prog_analysis = analyze_distribution(
            programacao.version, programacao.distribution_text, op_config, distances_path
        )
        cl_analysis = analyze_distribution(cl.version, cl.distribution_text, op_config, distances_path)
        unit_rows, changed_units_count = self._build_unit_rows(
            programacao.version, cl.version, prog_analysis["units"], cl_analysis["units"]
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
    def _fmt_value(value) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return str(value)

    @staticmethod
    def _escape_html(value: object) -> str:
        text = AppService._fmt_value(value)
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _html_table(headers: List[str], rows: List[List[object]]) -> str:
        head = "".join(f"<th>{AppService._escape_html(header)}</th>" for header in headers)
        body_rows = []
        for row in rows:
            cols = "".join(f"<td>{AppService._escape_html(cell)}</td>" for cell in row)
            body_rows.append(f"<tr>{cols}</tr>")
        return (
            "<table class='compare-table'>"
            f"<thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
        )

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
        html_parts = [
            """
            <html>
            <head>
            <style>
            body { font-family: Segoe UI, Arial, sans-serif; color: #1f2937; margin: 10px; }
            h2 { margin: 18px 0 8px; font-size: 18px; color: #0f172a; }
            .meta { margin-bottom: 14px; font-size: 13px; color: #475569; }
            .compare-table { border-collapse: collapse; width: 100%; margin: 8px 0 18px; table-layout: fixed; }
            .compare-table th { background: #e2e8f0; color: #0f172a; font-weight: 600; text-align: left; }
            .compare-table th, .compare-table td { border: 1px solid #cbd5e1; padding: 8px 10px; vertical-align: top; font-size: 12px; word-wrap: break-word; }
            .note { font-size: 12px; color: #475569; margin: 6px 0 12px; }
            </style>
            </head>
            <body>
            """,
            f"<div class='meta'><strong>Operacao:</strong> {self._escape_html(metadata.operacao_id)}<br>"
            f"<strong>Data:</strong> {self._escape_html(metadata.data_operacao)}</div>",
            "<h2>Resumo</h2>",
            self._html_table(
                ["INDICADOR", "PROGRAMACAO", "CONTROLADOR", "DIFERENCA"],
                [
                    ["Distancia total (NM)", programacao.metrics["total_distance_nm"], cl.metrics["total_distance_nm"], summary.delta_distancia_nm],
                    ["TMIB total", programacao.metrics["total_tmib"], cl.metrics["total_tmib"], summary.delta_total_tmib],
                    ["M9 total", programacao.metrics["total_m9"], cl.metrics["total_m9"], summary.delta_total_m9],
                    ["Plataformas completas", programacao.metrics["platforms_complete"], cl.metrics["platforms_complete"], summary.delta_platforms_complete],
                ],
            ),
            "<h2>Resumo Executivo</h2>",
            self._html_table(
                ["INFORMACAO", "PROGRAMACAO", "CONTROLADOR"],
                [
                    ["Operacao", metadata.operacao_id, metadata.operacao_id],
                    ["Data", metadata.data_operacao, metadata.data_operacao],
                    ["Unidades alteradas", summary.changed_units_count, summary.changed_units_count],
                    ["Programacao existe", "SIM" if summary.programacao_existe else "NAO", "-"],
                    ["Controlador existe", "-", "SIM" if summary.cl_oficial_existe else "NAO"],
                ],
            ),
            "<h2>Impacto Em Prioridades</h2>",
        ]
        if not priority_rows:
            html_parts.append("<div class='note'>Nenhuma unidade com prioridade definida nas duas versoes.</div>")
        else:
            html_parts.append(
                self._html_table(
                    ["INDICADOR", "PROGRAMACAO", "CONTROLADOR", "DIFERENCA"],
                    [
                        ["Unidades prioritarias avaliadas", summary.priority_units_count, summary.priority_units_count, 0],
                        ["Impacto agregado (min)", "-", "-", summary.priority_service_delta_minutes],
                    ],
                )
            )
        html_parts.extend(
            [
                "<h2>Diferencas Por Unidade</h2>",
                self._html_table(
                    ["UNIDADE", "PROGRAMACAO", "CONTROLADOR", "DIFERENCA"],
                    [
                        [
                            row["plataforma"],
                            f"TMIB {row['prog_tmib']} | M9 {row['prog_m9']}",
                            f"TMIB {row['cl_tmib']} | M9 {row['cl_m9']}",
                            f"TMIB {row['delta_tmib']:+} | M9 {row['delta_m9']:+}",
                        ]
                        for row in unit_rows
                    ],
                ),
                "<h2>Diferencas Por Embarcacao</h2>",
                self._html_table(
                    ["EMBARCACAO", "PROGRAMACAO", "CONTROLADOR", "DIFERENCA"],
                    [
                        [
                            row["embarcacao"],
                            f"Ativa {'SIM' if row['prog_ativa'] else 'NAO'} | Dist {self._fmt_value(row['prog_dist'])} | {row['prog_route'] or '-'}",
                            f"Ativa {'SIM' if row['cl_ativa'] else 'NAO'} | Dist {self._fmt_value(row['cl_dist'])} | {row['cl_route'] or '-'}",
                            f"Dist {row['delta_dist']:+}",
                        ]
                        for row in boat_rows
                    ],
                ),
                "</body></html>",
            ]
        )
        return "".join(html_parts)


def default_operation_version(version_name: str, user_name: str) -> OperationVersion:
    return OperationVersion(versao=version_name, usuario=user_name, criado_em=utc_now_iso())


def today_iso() -> str:
    return date.today().isoformat()
