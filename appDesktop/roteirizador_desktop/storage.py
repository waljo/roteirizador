from __future__ import annotations

import csv
import json
import os
import stat
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .domain import (
    AppConfig,
    ComparisonSummary,
    OperationMetadata,
    OperationVersion,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
)
from .runtime import app_config_path
from .runtime import default_storage_root
from .runtime import shared_app_config_path


LOCAL_CONFIG_PATH = app_config_path(".roteirizador_desktop_config.json")
SHARED_CONFIG_PATH = shared_app_config_path(".roteirizador_desktop_shared_config.json")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _append_csv(path: Path, header: Iterable[str], row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _remove_readonly_and_retry(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


class LocalConfigStore:
    def load(self) -> AppConfig:
        fixed_root = str(default_storage_root())
        return AppConfig(storage_root=fixed_root)

    def save(self, config: AppConfig) -> None:
        config = AppConfig(storage_root=str(default_storage_root()), version=config.version)
        _atomic_write_json(LOCAL_CONFIG_PATH, config.to_dict())
        try:
            _atomic_write_json(SHARED_CONFIG_PATH, config.to_dict())
        except OSError:
            # Shared config may be read-only in some installations; keep local config as fallback.
            pass


class NetworkStorage:
    def __init__(self, root: str):
        self.root = Path(root)
        self.config_dir = self.root / "config"
        self.operations_dir = self.root / "operacoes"
        self.indices_dir = self.root / "indices"
        self.logs_dir = self.root / "logs"

    def ensure_root(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.operations_dir.mkdir(parents=True, exist_ok=True)
        self.indices_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def config_path(self, name: str) -> Path:
        return self.config_dir / name

    def save_json_config(self, name: str, payload: Dict[str, Any]) -> None:
        _atomic_write_json(self.config_path(name), payload)

    def load_json_config(self, name: str) -> Dict[str, Any]:
        path = self.config_path(name)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def operation_dir(self, operation_id: str, operation_date: str) -> Path:
        year, month, day = operation_date.split("-")
        return self.operations_dir / year / month / day / operation_id

    def save_operation_metadata(self, metadata: OperationMetadata) -> None:
        op_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao)
        op_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(op_dir / "metadata.json", metadata.to_dict())

    def rename_operation(self, metadata: OperationMetadata, new_operation_id: str) -> OperationMetadata:
        old_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao)
        new_dir = self.operation_dir(new_operation_id, metadata.data_operacao)
        if new_dir.exists():
            raise FileExistsError(f"Ja existe uma operacao com id {new_operation_id}")
        if not old_dir.exists():
            raise FileNotFoundError(f"Operacao nao encontrada: {metadata.operacao_id}")
        old_dir.rename(new_dir)
        renamed = OperationMetadata(
            operacao_id=new_operation_id,
            data_operacao=metadata.data_operacao,
            criada_em=metadata.criada_em,
            status=metadata.status,
            display_name=metadata.display_name,
        )
        _atomic_write_json(new_dir / "metadata.json", renamed.to_dict())
        return renamed

    def update_operation_metadata(self, metadata: OperationMetadata) -> OperationMetadata:
        op_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao)
        if not op_dir.exists():
            raise FileNotFoundError(f"Operacao nao encontrada: {metadata.operacao_id}")
        _atomic_write_json(op_dir / "metadata.json", metadata.to_dict())
        return metadata

    def delete_operation(self, metadata: OperationMetadata) -> None:
        op_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao)
        if op_dir.exists():
            shutil.rmtree(op_dir, onerror=_remove_readonly_and_retry)

    def list_operations(self) -> List[OperationMetadata]:
        results: List[OperationMetadata] = []
        if not self.operations_dir.exists():
            return results
        for meta_path in sorted(self.operations_dir.rglob("metadata.json")):
            try:
                metadata = OperationMetadata.from_dict(
                    json.loads(meta_path.read_text(encoding="utf-8"))
                )
                if not metadata.display_name:
                    metadata = self._hydrate_display_name(metadata)
                results.append(metadata)
            except Exception:
                continue
        results.sort(key=lambda item: (item.data_operacao, item.operacao_id), reverse=True)
        return results

    def _hydrate_display_name(self, metadata: OperationMetadata) -> OperationMetadata:
        op_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao)
        labels: List[str] = []
        version_candidates = [
            (VERSION_PROGRAMACAO, "DISTPROG"),
            (VERSION_CL, "DISTCL"),
        ]
        for version_name, suffix in version_candidates:
            input_path = op_dir / version_name / "input.json"
            if not input_path.exists():
                continue
            try:
                version = OperationVersion.from_dict(
                    json.loads(input_path.read_text(encoding="utf-8"))
                )
            except Exception:
                continue
            user = (version.usuario or "").strip()
            if not user:
                continue
            labels.append(f"{user} - {suffix}")
        if not labels:
            return metadata
        return OperationMetadata(
            operacao_id=metadata.operacao_id,
            data_operacao=metadata.data_operacao,
            criada_em=metadata.criada_em,
            status=metadata.status,
            display_name=" | ".join(labels),
        )

    def save_version(
        self,
        metadata: OperationMetadata,
        bundle: VersionBundle,
        imported_csv_path: Optional[Path] = None,
    ) -> None:
        version_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao) / bundle.version.versao
        version_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(version_dir / "input.json", bundle.version.to_dict())
        if bundle.metrics is not None:
            _atomic_write_json(version_dir / "metricas.json", bundle.metrics)
        if bundle.distribution_text:
            _atomic_write_text(version_dir / "distribuicao.txt", bundle.distribution_text)
        if imported_csv_path and imported_csv_path.exists():
            shutil.copy2(imported_csv_path, version_dir / imported_csv_path.name)

    def load_version(self, metadata: OperationMetadata, version_name: str) -> Optional[VersionBundle]:
        version_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao) / version_name
        input_path = version_dir / "input.json"
        if not input_path.exists():
            return None
        version = OperationVersion.from_dict(json.loads(input_path.read_text(encoding="utf-8")))
        metrics = None
        metrics_path = version_dir / "metricas.json"
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        distribution_text = ""
        output_path = version_dir / "distribuicao.txt"
        if output_path.exists():
            distribution_text = output_path.read_text(encoding="utf-8")
        imported = ""
        for candidate in version_dir.glob("*.csv"):
            imported = candidate.name
            break
        return VersionBundle(
            version=version,
            distribution_text=distribution_text,
            metrics=metrics,
            imported_csv_name=imported,
        )

    def save_comparison(
        self,
        metadata: OperationMetadata,
        summary: ComparisonSummary,
        details_text: str,
    ) -> None:
        compare_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao) / "comparacao"
        compare_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(compare_dir / "resumo.json", summary.to_dict())
        _atomic_write_text(compare_dir / "diferencas.txt", details_text)
        self._update_indices(metadata, summary)

    def load_comparison(self, metadata: OperationMetadata) -> Optional[Dict[str, Any]]:
        compare_dir = self.operation_dir(metadata.operacao_id, metadata.data_operacao) / "comparacao"
        summary_path = compare_dir / "resumo.json"
        if not summary_path.exists():
            return None
        details = ""
        detail_path = compare_dir / "diferencas.txt"
        if detail_path.exists():
            details = detail_path.read_text(encoding="utf-8")
        return {
            "summary": json.loads(summary_path.read_text(encoding="utf-8")),
            "details": details,
        }

    def _update_indices(self, metadata: OperationMetadata, summary: ComparisonSummary) -> None:
        _append_csv(
            self.indices_dir / "comparacoes.csv",
            header=[
                "operacao_id",
                "data_operacao",
                "generated_at",
                "delta_distancia_nm",
                "delta_total_tmib",
                "delta_total_m9",
                "delta_platforms_complete",
                "delta_service_minutes_complete",
                "changed_units_count",
                "priority_units_count",
                "priority_service_delta_minutes",
            ],
            row={
                "operacao_id": metadata.operacao_id,
                "data_operacao": metadata.data_operacao,
                "generated_at": summary.generated_at,
                "delta_distancia_nm": summary.delta_distancia_nm,
                "delta_total_tmib": summary.delta_total_tmib,
                "delta_total_m9": summary.delta_total_m9,
                "delta_platforms_complete": summary.delta_platforms_complete,
                "delta_service_minutes_complete": summary.delta_service_minutes_complete,
                "changed_units_count": summary.changed_units_count,
                "priority_units_count": summary.priority_units_count,
                "priority_service_delta_minutes": summary.priority_service_delta_minutes,
            },
        )
