"""
AIMMS Model Interface (Streamlit Application)

This module provides a Streamlit-based web interface for the AIMMS optimization model.
It allows users to:
- Configure model parameters (planning horizon, number of physicians, scenarios, etc.)
- Upload input data files (CSV format)
- Execute the AIMMS optimization model
- Visualize and download optimization results

The application handles data transformation between CSV files and AIMMS text format,
manages runtime directories, and provides a user-friendly interface for model execution.

Key Components:
- Configuration management (config.yaml)
- CSV file handling and validation
- AIMMS integration (batch execution)
- Results parsing and visualization
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import re

import pandas as pd
import streamlit as st
from datetime import datetime
import shutil
import numpy as np

# Root paths
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "streamlit_app"
RUNTIME_INPUT_DIR = APP_ROOT / "runtime_input"
RUNTIME_OUTPUT_DIR = APP_ROOT / "runtime_output"
CONFIG_PATH = APP_ROOT / "config.yaml"


def ensure_dirs() -> None:
	"""
	Create runtime directories if they don't exist.
	
	Ensures that both runtime_input and runtime_output directories exist
	before the application attempts to read or write files. This prevents
	file I/O errors during execution.
	"""
	RUNTIME_INPUT_DIR.mkdir(parents=True, exist_ok=True)
	RUNTIME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
	"""
	Load configuration from config.yaml if present; otherwise return defaults.
	
	The configuration file allows users to set persistent paths and settings
	without modifying the code. If the config file doesn't exist, sensible
	defaults are provided, including automatic detection of the project file
	location.
	
	Returns:
		dict: Configuration dictionary with the following keys:
			- aimms_exe_path (str): Absolute path to AIMMS executable (Windows)
			- project_path (str): Absolute path to .aimms project file
			- run_procedure (str): AIMMS procedure to run (default: "RunOptimizationFromCSV")
			- use_powershell (bool): Whether to use PowerShell for execution (default: True)
	
	Note:
		The function automatically detects the project file location, preferring
		the new "model_aimms" folder structure over the legacy folder name.
	"""
	import yaml

	# Prefer new folder name; fallback to legacy folder if not yet renamed
	preferred_project = (REPO_ROOT / "model_aimms" / "CPLAN.aimms").resolve()
	legacy_project = (REPO_ROOT / "CPLAN Stochastic SAA CERTO StoHard Cap8" / "CPLAN.aimms").resolve()
	default_project_path = str(preferred_project if preferred_project.exists() else legacy_project)

	defaults = {
		"aimms_exe_path": "",
		"project_path": default_project_path,
		"run_procedure": "RunOptimizationFromCSV",
		"use_powershell": True,
	}
	if CONFIG_PATH.exists():
		with open(CONFIG_PATH, "r", encoding="utf-8") as f:
			user_cfg = yaml.safe_load(f) or {}
		defaults.update({k: v for k, v in user_cfg.items() if v is not None})
	return defaults


def save_uploaded_csv(file, target_name: str) -> Optional[Path]:
	"""
	Save an uploaded CSV file to the runtime_input directory.
	
	This function handles file uploads from the Streamlit interface, reading
	the CSV data and saving it with a standardized filename in the runtime
	input directory.
	
	Args:
		file: Streamlit UploadedFile object or file-like object containing CSV data
		target_name (str): Standardized filename to use (e.g., "Demand.csv")
	
	Returns:
		Optional[Path]: Path to the saved file if successful, None otherwise
	
	Note:
		Errors during file reading or writing are caught and displayed to the user
		via Streamlit error messages.
	"""
	try:
		df = pd.read_csv(file)
		target_path = RUNTIME_INPUT_DIR / target_name
		df.to_csv(target_path, index=False)
		return target_path
	except Exception as e:
		st.error(f"Error saving {target_name}: {e}")
		return None


def save_scalar_to_csv(name: str, value) -> Path:
	"""
	Save a scalar parameter value as a single-row CSV file.
	
	This function converts scalar model parameters (like PlanHorizon, NumberOfPhysicians)
	into CSV format for consistent ingestion by the AIMMS model. The CSV contains
	a single row with the parameter name as the column header and the value.
	
	Args:
		name (str): Parameter name (e.g., "PlanHorizon", "Alpha")
		value: Parameter value (can be int, float, or other numeric type)
	
	Returns:
		Path: Path to the created CSV file
	
	Example:
		>>> save_scalar_to_csv("PlanHorizon", 111)
		Creates a CSV file with: PlanHorizon
		                           111
	"""
	target_path = RUNTIME_INPUT_DIR / f"{name}.csv"
	pd.DataFrame([{name: value}]).to_csv(target_path, index=False)
	return target_path


def append_aimms_text_for_scalar(buf: list[str], name: str, value) -> None:
	"""
	Append AIMMS text format assignment for a scalar parameter to a buffer.
	
	This function generates AIMMS syntax for scalar parameter assignments.
	The format is: "ParameterName := value ;"
	
	Args:
		buf (list[str]): List of strings (buffer) to append the AIMMS text to
		name (str): Parameter name in AIMMS model
		value: Parameter value (None values are skipped)
	
	Note:
		Silently handles errors and skips None values to allow graceful
		processing of optional parameters.
	"""
	try:
		if value is None:
			return
		buf.append(f"{name} := {value} ;\n")
	except Exception:
		pass


def append_aimms_text_for_table(buf: list[str], name: str, path: Path, index_cols: list[str]) -> None:
	"""
	Append AIMMS text format assignment for a table/parameter to a buffer.
	
	This function reads a CSV file containing parameter data and converts it
	to AIMMS text format. It handles column name normalization (accepting
	variations like 'Valor', 'VALOR', 'value') and applies special sanitization
	for binary parameters (TwoWeekPattern, PhysicianAssign2) and integer
	parameters (SurgeryCapacity).
	
	Args:
		buf (list[str]): List of strings (buffer) to append the AIMMS text to
		name (str): Parameter name in AIMMS model
		path (Path): Path to the CSV file containing the parameter data
		index_cols (list[str]): List of column names that form the parameter indices
			(e.g., ["j", "t", "k"] for a 3-dimensional parameter)
	
	The function generates AIMMS syntax like:
		ParameterName := data
		{ ( index1, index2, ... ) : value,
		  ( index1, index2, ... ) : value,
		  ... } ;
	
	Special handling:
		- TwoWeekPattern, PhysicianAssign2: Values are coerced to binary {0,1}
		- SurgeryCapacity: Values are coerced to nonnegative integers
		- General parameters: Values are kept as-is (can be floats)
	
	Note:
		If the CSV is missing required columns or cannot be processed, a warning
		is displayed to the user and the function returns without appending anything.
	"""
	try:
		df = pd.read_csv(path)
		# Normalize column names (accept variations like 'Valor', 'VALOR', 'T', 'J', 'K', 'S')
		def canon_cols(frame: pd.DataFrame) -> pd.DataFrame:
			col_map = {}
			for c in frame.columns:
				lc = str(c).strip().lower()
				if lc in ("t", "j", "k", "s"):
					col_map[c] = lc
				elif lc in ("valor", "value", "val"):
					col_map[c] = "value"
			if col_map:
				return frame.rename(columns=col_map)
			return frame
		df = canon_cols(df)
		if not set(index_cols).issubset(df.columns) or "value" not in df.columns:
			st.warning(f"{name}: CSV must contain columns {index_cols} and 'value'.")
			return
		# Specific sanitization for binary/integer parameters
		if name == "TwoWeekPattern":
			df_local = df[index_cols + ["value"]].copy()
			for c in index_cols:
				df_local[c] = pd.to_numeric(df_local[c], errors="coerce")
			df_local["value"] = pd.to_numeric(df_local["value"], errors="coerce")
			# Remove rows with NaN in indices or value
			df_local = df_local.dropna(subset=index_cols + ["value"])
			# Convert indices to int and value to {0,1}
			for c in index_cols:
				df_local[c] = df_local[c].astype(int)
			df_local["value"] = df_local["value"].round().clip(0, 1).astype(int)
			entries = []
			for _, row in df_local.iterrows():
				indices = ", ".join(str(int(row[c])) for c in index_cols)
				entries.append(f"( {indices} ) : {int(row['value'])}")
		elif name == "PhysicianAssign2":
			# Ensure binary {0,1} and integer indices
			df_local = df[index_cols + ["value"]].copy()
			for c in index_cols:
				df_local[c] = pd.to_numeric(df_local[c], errors="coerce")
			df_local["value"] = pd.to_numeric(df_local["value"], errors="coerce")
			df_local = df_local.dropna(subset=index_cols + ["value"])
			for c in index_cols:
				df_local[c] = df_local[c].astype(int)
			df_local["value"] = df_local["value"].round().clip(0, 1).astype(int)
			entries = []
			for _, row in df_local.iterrows():
				indices = ", ".join(str(int(row[c])) for c in index_cols)
				entries.append(f"( {indices} ) : {int(row['value'])}")
		elif name == "SurgeryCapacity":
			# Ensure integers and nonnegative values; accept k=1 and k=5 (or others if present)
			df_local = df[index_cols + ["value"]].copy()
			for c in index_cols:
				df_local[c] = pd.to_numeric(df_local[c], errors="coerce")
			df_local["value"] = pd.to_numeric(df_local["value"], errors="coerce")
			df_local = df_local.dropna(subset=index_cols + ["value"])
			for c in index_cols:
				df_local[c] = df_local[c].astype(int)
			# Capacity must be nonnegative integer
			df_local["value"] = df_local["value"].round().clip(lower=0).astype(int)
			entries = []
			for _, row in df_local.iterrows():
				indices = ", ".join(str(int(row[c])) for c in index_cols)
				entries.append(f"( {indices} ) : {int(row['value'])}")
		else:
			entries = []
			for _, row in df.iterrows():
				def format_index(val):
					try:
						v = pd.to_numeric(val, errors="coerce")
						if pd.notna(v) and float(v).is_integer():
							return str(int(v))
					except Exception:
						pass
					return str(val)
				indices = ", ".join(format_index(row[c]) for c in index_cols)
				# Value: keep as-is for general parameters
				entries.append(f"( {indices} ) : {row['value']}")
		if entries:
			buf.append(f"{name} := data\n" + "{ " + ",\n  ".join(entries) + " } ;\n\n")
	except Exception as e:
		st.warning(f"Failed to compose AIMMS text for {name}: {e}")

def append_aimms_text_for_set(buf: list[str], name: str, path: Path, col: str) -> None:
	"""
	Append AIMMS text format assignment for a set to a buffer.
	
	This function reads a CSV file containing set elements and converts them
	to AIMMS set syntax. Sets in AIMMS are collections of elements, typically
	used to define index domains (e.g., the set of assignment types k).
	
	Args:
		buf (list[str]): List of strings (buffer) to append the AIMMS text to
		name (str): Set name in AIMMS model
		path (Path): Path to the CSV file containing set elements
		col (str): Column name containing the set element values
	
	The function generates AIMMS syntax like:
		SetName := { element1, element2, element3, ... } ;
	
	Note:
		- Values are sorted and converted to integers
		- Duplicate values are removed
		- If the CSV is missing the required column or cannot be processed,
		  a warning is displayed and the function returns without appending
	"""
	try:
		df = pd.read_csv(path)
		if col not in df.columns:
			st.warning(f"{name}: CSV must contain column '{col}'.")
			return
		values = sorted(pd.to_numeric(df[col], errors="coerce").dropna().astype(int).unique().tolist())
		if values:
			# For sets, AIMMS expects the syntax without the 'data' keyword
			values_str = ", ".join(str(v) for v in values)
			buf.append(f"{name} := {{ {values_str} }} ;\n")
	except Exception as e:
		st.warning(f"Failed to compose AIMMS text for set {name}: {e}")


def build_and_write_aimms_input_text(scalars: dict, csv_paths: dict) -> Path:
	"""
	Build and write a consolidated AIMMS text input file from all model inputs.
	
	This function aggregates all scalar parameters and CSV-based parameters into
	a single AIMMS text format file (aimms_input.txt). This consolidated file can
	be read by the AIMMS model to populate all parameters at once.
	
	Args:
		scalars (dict): Dictionary mapping scalar parameter names to their values
			(e.g., {"PlanHorizon": 111, "Alpha": 0.68})
		csv_paths (dict): Dictionary mapping parameter names to their CSV file paths
			(e.g., {"Demand": Path("Demand.csv"), "ClinicCapacity": Path("ClinicCapacity.csv")})
	
	Returns:
		Path: Path to the created aimms_input.txt file
	
	The function processes inputs in this order:
		1. Scalars (simple assignments)
		2. Sets (must be defined before parameters that reference them)
		3. Tables/Parameters (multi-dimensional data)
	
	Note:
		The output file is written to runtime_input/aimms_input.txt and uses
		UTF-8 encoding to support international characters.
	"""
	lines: list[str] = []
	# Scalars
	for n, v in scalars.items():
		append_aimms_text_for_scalar(lines, n, v)
	# Sets (handled before tables, so parameters can reference them)
	if csv_paths.get("Assign") and csv_paths["Assign"].exists():
		append_aimms_text_for_set(lines, "Assign", csv_paths["Assign"], "k")
	# Tables
	# Expected CSV schemas
	mapping = {
		"Demand": (["t", "s"], csv_paths.get("Demand")),
		"ClinicCapacity": (["j", "t"], csv_paths.get("ClinicCapacity")),
		"SurgeryCapacity": (["j", "t", "k"], csv_paths.get("SurgeryCapacity")),
		"InicialQueueSurgery": (["j"], csv_paths.get("InicialQueueSurgery")),
		"MinARE": (["j"], csv_paths.get("MinARE")),
		"MaxOR": (["t"], csv_paths.get("MaxOR")),
		"PhysicianAssign2": (["j", "t", "k"], csv_paths.get("PhysicianAssign2")),
		"TwoWeekPattern": (["t"], csv_paths.get("TwoWeekPattern")),
		"isOR": (["k"], csv_paths.get("isOR")),
		"isClinic": (["k"], csv_paths.get("isClinic")),
		"isCO": (["k"], csv_paths.get("isCO")),
		"dayInTau": (["t"], csv_paths.get("dayInTau")),
		"ORMultiplier": (["k"], csv_paths.get("ORMultiplier")),
		"MinTypePerDay": (["j","k"], csv_paths.get("MinTypePerDay")),
	}
	for ident, (cols, pth) in mapping.items():
		if pth and pth.exists():
			append_aimms_text_for_table(lines, ident, pth, cols)
	# Write file
	target = RUNTIME_INPUT_DIR / "aimms_input.txt"
	target.write_text("".join(lines), encoding="utf-8")
	return target


def compute_max_t_from_csvs(csv_paths: dict) -> Optional[int]:
	"""
	Compute the maximum time period (t) value across all relevant CSV files.
	
	This function scans multiple CSV files that contain time period data and
	returns the highest 't' value found. This is used to validate that the
	planning horizon (PlanHorizon) is consistent with the data coverage.
	
	Args:
		csv_paths (dict): Dictionary mapping parameter names to CSV file paths.
			Only files for parameters that contain time periods are checked:
			- Demand
			- ClinicCapacity
			- SurgeryCapacity
			- PhysicianAssign2
			- MaxOR
			- TwoWeekPattern
	
	Returns:
		Optional[int]: Maximum 't' value found across all CSVs, or None if no
			valid 't' values are found
	
	Note:
		This function is used for validation to warn users if their PlanHorizon
		parameter doesn't match the data coverage in their CSV files.
	"""
	max_t: Optional[int] = None
	for key in ["Demand", "ClinicCapacity", "SurgeryCapacity", "PhysicianAssign2", "MaxOR", "TwoWeekPattern"]:
		p = csv_paths.get(key)
		if not p or not p.exists():
			continue
		try:
			df = pd.read_csv(p)
			if "t" in df.columns:
				local_max = pd.to_numeric(df["t"], errors="coerce").dropna().astype(int).max()
				if pd.notna(local_max):
					max_t = local_max if max_t is None else max(max_t, int(local_max))
		except Exception:
			continue
	return max_t


def compute_max_s_from_demand(demand_path: Path) -> Optional[int]:
	"""
	Compute the maximum scenario (s) value from the Demand CSV file.
	
	This function reads the Demand.csv file and finds the highest scenario
	index. This is used to validate that the TotalScenarios parameter matches
	the number of scenarios in the demand data.
	
	Args:
		demand_path (Path): Path to the Demand.csv file
	
	Returns:
		Optional[int]: Maximum 's' (scenario) value found in the file, or None
			if the file doesn't exist or doesn't contain valid scenario data
	
	Note:
		This function is used for validation to warn users if their TotalScenarios
		parameter doesn't match the scenario coverage in their Demand.csv file.
	"""
	if not demand_path.exists():
		return None
	try:
		df = pd.read_csv(demand_path)
		if "s" in df.columns:
			local_max = pd.to_numeric(df["s"], errors="coerce").dropna().astype(int).max()
			return int(local_max) if pd.notna(local_max) else None
	except Exception:
		return None
	return None

def write_generated_csv(name: str, dest_path: Path, plan_horizon: int, total_scenarios: int, num_physicians: int) -> bool:
	"""
	Generate a minimal but valid CSV file for a given parameter name.
	
	This function creates default CSV files when user-provided data is not available.
	The generated files contain reasonable default values that allow the model to run,
	though they may not represent real-world data.
	
	Args:
		name (str): Parameter name (e.g., "Demand", "ClinicCapacity", "SurgeryCapacity")
		dest_path (Path): Path where the CSV file should be written
		plan_horizon (int): Planning horizon in days (used for time-based parameters)
		total_scenarios (int): Number of scenarios (used for scenario-based parameters)
		num_physicians (int): Number of physicians (used for physician-based parameters)
	
	Returns:
		bool: True if the CSV was successfully generated, False otherwise
	
	Supported parameter names and their default generation logic:
		- Assign: Default assignment types {1,2,3,4,5,6,7}
		- Demand: Poisson-distributed demand per weekday, zero on weekends
		- ClinicCapacity: Default capacity of 8 patients per physician per day
		- SurgeryCapacity: Default OR capacity of 3 for both OR types (k=1 and k=5)
		- PhysicianAssign2: Minimal default allowing Clinic (k=2) and basic OR (k=1) for all physicians and days to ensure feasibility
		- MaxOR: Default maximum of 2 ORs per day
		- InicialQueueSurgery: Default queue values cycling through a pattern
		- MinARE: Default of 0 for all physicians
		- TwoWeekPattern: Default of 0 (no pattern) for all days
		- isOR, isClinic, isCO: Default sets based on standard assignment types
		- dayInTau: Empty by default
		- ORMultiplier: Default weights (k=1→1, k=5→2)
		- MinTypePerDay: Empty by default
	
	Note:
		Generated files are intended for testing and demonstration. For production
		use, users should provide their own data files.
	"""
	try:
		if name == "Assign":
			rows = [{"k": k} for k in [1,2,3,4,5,6,7]]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "Demand":
			# Poisson per weekday (Mon..Fri) with given means; weekends = 0; assume t=1 is Monday
			lambdas = [10.04, 16.96, 12.07, 15.68, 7.92]  # Mon..Fri
			rows = []
			for t in range(1, plan_horizon + 1):
				day_idx = (t - 1) % 7  # 0=Mon ... 6=Sun
				lmb = lambdas[day_idx] if day_idx < 5 else 0.0
				for s in range(1, total_scenarios + 1):
					val = int(np.random.poisson(lmb)) if lmb > 0 else 0
					rows.append({"t": t, "s": s, "value": val})
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "ClinicCapacity":
			rows = [{"j": j, "t": t, "value": 8} for j in range(1, num_physicians + 1) for t in range(1, plan_horizon + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "SurgeryCapacity":
			# Default OR capacities: 3 for both OR types (k=1 and k=5)
			rows = [{"j": j, "t": t, "k": 1, "value": 3} for j in range(1, num_physicians + 1) for t in range(1, plan_horizon + 1)]
			rows += [{"j": j, "t": t, "k": 5, "value": 3} for j in range(1, num_physicians + 1) for t in range(1, plan_horizon + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "PhysicianAssign2":
			# Minimal default to ensure feasibility: allow Clinic (k=2) and basic OR (k=1) for all physicians and days
			# This ensures Clinic and Surgery variables are not zeroed out
			# More conservative than the previous version: does not include 2OR (k=5) or Admin/PTO (k=3) automatically
			rows = []
			for j in range(1, num_physicians + 1):
				for t in range(1, plan_horizon + 1):
					# Allow Clinic (k=2) - required for Clinic variable
					rows.append({"j": j, "t": t, "k": 2, "value": 1})
					# Allow basic OR (k=1) - required for Surgery variable
					rows.append({"j": j, "t": t, "k": 1, "value": 1})
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "MaxOR":
			rows = [{"t": t, "value": 2} for t in range(1, plan_horizon + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "InicialQueueSurgery":
			defaults = [21, 20, 15, 14, 20, 23, 18, 10, 16, 11]
			rows = [{"j": j, "value": int(defaults[(j - 1) % len(defaults)])} for j in range(1, num_physicians + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "MinARE":
			rows = [{"j": j, "value": 0} for j in range(1, num_physicians + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "TwoWeekPattern":
			rows = [{"t": t, "value": 0} for t in range(1, plan_horizon + 1)]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "isOR":
			rows = [{"k": k, "value": 1} for k in [1,5]]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "isClinic":
			rows = [{"k": 2, "value": 1}]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "isCO":
			rows = [{"k": k, "value": 1} for k in [1,2,5]]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "dayInTau":
			# default empty
			pd.DataFrame(columns=["t","value"]).to_csv(dest_path, index=False)
			return True
		if name == "ORMultiplier":
			# default: 1 for k=1, 2 for k=5 (classical 2OR weight)
			rows = [{"k": 1, "value": 1}, {"k": 5, "value": 2}]
			pd.DataFrame(rows).to_csv(dest_path, index=False)
			return True
		if name == "MinTypePerDay":
			# default: empty (user provides per physician/type minima over window; excludes Clinic/OR)
			pd.DataFrame(columns=["j","k","value"]).to_csv(dest_path, index=False)
			return True
	except Exception:
		return False
	return False


def ensure_template_if_missing(name: str, dest_path: Path, plan_horizon: int, total_scenarios: int, num_physicians: int) -> bool:
	"""
	Ensure a CSV file exists in runtime_input, using template or generating if needed.
	
	This function implements a fallback chain for missing input files:
	1. If the file already exists in runtime_input, do nothing
	2. If a template exists in templates/, copy it
	3. Otherwise, generate a default CSV using write_generated_csv()
	
	Args:
		name (str): Parameter name (e.g., "Demand", "ClinicCapacity")
		dest_path (Path): Destination path in runtime_input directory
		plan_horizon (int): Planning horizon for generated files
		total_scenarios (int): Number of scenarios for generated files
		num_physicians (int): Number of physicians for generated files
	
	Returns:
		bool: True if a template was copied or a file was generated, False if
			the file already existed or an error occurred
	"""
	template_path = APP_ROOT / "templates" / f"{name}.csv"
	if dest_path.exists():
		return False
	if template_path.exists():
		try:
			shutil.copyfile(template_path, dest_path)
			return True
		except Exception:
			return False
	# If no template file, generate one on the fly based on defaults
	return write_generated_csv(name, dest_path, plan_horizon, total_scenarios, num_physicians)


def populate_templates_dir(plan_horizon: int, total_scenarios: int, num_physicians: int) -> list[str]:
	"""
	Populate the templates directory with default CSV files if they don't exist.
	
	This function ensures that the templates/ directory contains default CSV files
	for all required parameters. These templates can be used as starting points
	or examples for users preparing their own data.
	
	Args:
		plan_horizon (int): Planning horizon for generated template files
		total_scenarios (int): Number of scenarios for generated template files
		num_physicians (int): Number of physicians for generated template files
	
	Returns:
		list[str]: List of parameter names for which template files were created
			(empty list if all templates already existed)
	
	Note:
		This function only creates files that don't already exist, preserving
		any user-modified templates.
	"""
	created: list[str] = []
	templates_dir = APP_ROOT / "templates"
	templates_dir.mkdir(parents=True, exist_ok=True)
	for key in ["Assign", "Demand", "ClinicCapacity", "SurgeryCapacity", "PhysicianAssign2", "MaxOR", "TwoWeekPattern", "InicialQueueSurgery", "MinARE", "ORMultiplier", "MinTypePerDay", "isOR", "isClinic", "isCO", "dayInTau"]:
		target = templates_dir / f"{key}.csv"
		if not target.exists():
			if write_generated_csv(key, target, plan_horizon, total_scenarios, num_physicians):
				created.append(key)
	return created


def parse_aimms_output(output_path: Path) -> Dict:
	"""
	Parse AIMMS output text file and extract variables and objective function value.
	
	This function reads the aimms_output.txt file generated by AIMMS and extracts:
	- The objective function value (Z)
	- All decision variables and their values
	
	The parser handles AIMMS text format syntax for both scalars and multi-dimensional
	parameters, extracting index tuples and corresponding values.
	
	Args:
		output_path (Path): Path to the aimms_output.txt file
	
	Returns:
		Dict: Dictionary with the following structure:
			{
				'Z': float or None,  # Objective function value
				'variables': {
					'x': [(indices, value), ...],  # List of (index_tuple, value) pairs
					'clinic': [(indices, value), ...],
					'surgery': [(indices, value), ...],
					...
				}
			}
	
	Supported variables:
		- x: Binary assignment variable (j, t, k)
		- clinic: Clinic appointments (j, t, s)
		- surgery: Surgeries performed (j, t, s)
		- surgerydemand: Surgery demand (j, t, s)
		- clinicwait: Clinic queue length (t, s)
		- surgerywait: Surgery queue length (j, t, s)
	
	Note:
		The function uses regex patterns to extract data blocks and parse entries.
		If parsing fails for a variable, it is omitted from the results.
	"""
	result = {
		'Z': None,
		'variables': {}
	}
	
	if not output_path.exists():
		return result
	
	content = output_path.read_text(encoding='utf-8', errors='replace')
	
	# Extract Z (objective function) - can be on same line or next line
	z_match = re.search(r'Z\s*:=\s*([0-9.]+(?:[eE][+-]?[0-9]+)?)\s*;', content)
	if z_match:
		try:
			result['Z'] = float(z_match.group(1))
		except ValueError:
			pass
	
	# Variable patterns: variable_name(indices) := data { ... }
	# Variables to extract: x, clinic, surgery, surgerydemand, clinicwait, surgerywait
	variable_names = ['x', 'clinic', 'surgery', 'surgerydemand', 'clinicwait', 'surgerywait']
	
	for var_name in variable_names:
		# Pattern to match: var_name(indices) := data { ... } ;
		# More flexible pattern that handles multi-line data blocks
		pattern = rf'{var_name}\s*\([^)]+\)\s*:=\s*data\s*\{{(.*?)\}}\s*;'
		match = re.search(pattern, content, re.DOTALL)
		
		if match:
			data_block = match.group(1).strip()
			
			# Handle empty data blocks
			if not data_block or data_block == '':
				result['variables'][var_name] = []
				continue
			
			# Parse entries: ( index1, index2, ... ) : value
			entries = []
			# Pattern to match entries that may span lines
			# Allow for spaces around colons and values
			entry_pattern = r'\(\s*([^)]+)\s*\)\s*:\s*([0-9.]+(?:[eE][+-]?[0-9]+)?)\s*,?'
			
			for entry_match in re.finditer(entry_pattern, data_block):
				indices_str = entry_match.group(1)
				value_str = entry_match.group(2)
				
				# Parse indices (comma-separated, may have whitespace)
				try:
					indices = [int(i.strip()) for i in indices_str.split(',') if i.strip()]
					value = float(value_str.strip())
					entries.append((indices, value))
				except (ValueError, AttributeError):
					continue
			
			if entries:
				result['variables'][var_name] = entries
			else:
				# Store empty list to indicate variable exists but has no data
				result['variables'][var_name] = []
	
	return result


def variable_to_csv(var_name: str, entries: List[Tuple[List[int], float]]) -> pd.DataFrame:
	"""
	Convert parsed variable entries to a pandas DataFrame with appropriate columns.
	
	This function takes the raw parsed variable data (list of index-value tuples)
	and converts it into a structured DataFrame with named columns based on the
	variable type.
	
	Args:
		var_name (str): Variable name (e.g., "x", "clinic", "surgery")
		entries (List[Tuple[List[int], float]]): List of (indices, value) tuples
			where indices is a list of index values and value is the parameter value
	
	Returns:
		pd.DataFrame: DataFrame with columns:
			- For "x": ['j', 't', 'k', 'value']
			- For "clinic", "surgery", "surgerydemand", "surgerywait": ['j', 't', 's', 'value']
			- For "clinicwait": ['t', 's', 'value']
			- For other variables: ['index_1', 'index_2', ..., 'value']
	
	Note:
		Returns an empty DataFrame if entries is empty.
	"""
	if not entries:
		return pd.DataFrame()
	
	# Determine number of indices from first entry
	num_indices = len(entries[0][0])
	
	# Create column names
	if var_name == 'x':
		cols = ['j', 't', 'k']
	elif var_name == 'clinic':
		cols = ['j', 't', 's']
	elif var_name == 'surgery':
		cols = ['j', 't', 's']
	elif var_name == 'surgerydemand':
		cols = ['j', 't', 's']
	elif var_name == 'clinicwait':
		cols = ['t', 's']
	elif var_name == 'surgerywait':
		cols = ['j', 't', 's']
	else:
		cols = [f'index_{i+1}' for i in range(num_indices)]
	
	cols.append('value')
	
	# Build rows
	rows = []
	for indices, value in entries:
		row = list(indices) + [value]
		rows.append(row)
	
	df = pd.DataFrame(rows, columns=cols)
	return df


def create_csvs_from_output(output_path: Path, output_dir: Path) -> Dict[str, Path]:
	"""
	Parse AIMMS output file and create CSV files for each extracted variable.
	
	This function orchestrates the parsing and CSV creation process:
	1. Parses the AIMMS output text file
	2. Converts each variable to a DataFrame
	3. Writes CSV files for each non-empty variable
	4. Returns a mapping of variable names to their CSV file paths
	
	Args:
		output_path (Path): Path to the aimms_output.txt file
		output_dir (Path): Directory where CSV files should be written
	
	Returns:
		Dict[str, Path]: Dictionary mapping variable names to their CSV file paths.
			Only variables with non-empty data are included.
	
	Example:
		>>> csv_paths = create_csvs_from_output(output_file, RUNTIME_OUTPUT_DIR)
		>>> # csv_paths might be:
		>>> # {'x': Path('x.csv'), 'clinic': Path('clinic.csv'), ...}
	"""
	output_dir.mkdir(parents=True, exist_ok=True)
	
	parsed = parse_aimms_output(output_path)
	csv_paths = {}
	
	for var_name, entries in parsed['variables'].items():
		df = variable_to_csv(var_name, entries)
		if not df.empty:
			csv_path = output_dir / f"{var_name}.csv"
			df.to_csv(csv_path, index=False)
			csv_paths[var_name] = csv_path
	
	return csv_paths


def run_aimms(aimms_exe_path: str, project_path: str, procedure: str, use_powershell: Optional[bool] = None) -> subprocess.CompletedProcess:
	"""
	Invoke AIMMS in batch mode to run a specific procedure.
	
	This function executes the AIMMS optimization model by calling the AIMMS
	executable with batch mode flags. It handles path conversion between Windows
	and WSL (Windows Subsystem for Linux) formats and can optionally use PowerShell
	for better path handling.
	
	Args:
		aimms_exe_path (str): Absolute path to the AIMMS executable (Windows .exe)
		project_path (str): Absolute path to the .aimms project file
		procedure (str): Name of the AIMMS procedure to execute
			(e.g., "RunOptimizationFromCSV")
		use_powershell (Optional[bool]): Whether to use PowerShell for execution.
			If None, defaults to True on WSL, False on native Windows.
	
	Returns:
		subprocess.CompletedProcess: Completed process object with stdout, stderr,
			and returncode attributes
	
	Raises:
		FileNotFoundError: If the AIMMS executable or project file cannot be found
	
	Note:
		- Command-line flags: -B (batch mode), -R (run procedure)
		- Output encoding is set to latin-1 to handle AIMMS/PowerShell character encoding
		- Path conversion handles both Windows (C:\...) and WSL (/mnt/c/...) formats
		- PowerShell is recommended when running from WSL to properly handle Windows paths
	"""
	def is_blank(s: Optional[str]) -> bool:
		return s is None or str(s).strip() == ""

	def to_windows_path_str(p: str) -> str:
		# Convert /mnt/c/Users/... -> C:\Users\...
		if p.startswith("/mnt/") and len(p) > 7:
			drive = p[5]
			rest = p[7:]
			bs = "\\"
			rest_win = rest.replace("/", bs)
			return f"{drive.upper()}:{bs}{rest_win}"
		return p

	def to_wsl_path_str(p: str) -> str:
		# Convert C:\Users\... -> /mnt/c/Users/...
		if len(p) >= 3 and p[1:3] == ":\\":
			drive = p[0].lower()
			rest = p[3:].replace("\\", "/")
			return f"/mnt/{drive}/{rest}"
		return p

	if is_blank(aimms_exe_path):
		raise FileNotFoundError("AIMMS.exe path not provided.")
	if is_blank(project_path):
		raise FileNotFoundError(".aimms project path not provided.")

	# Validate existence (try both native and translated paths)
	exe_path_native = Path(aimms_exe_path)
	exe_path_alt = Path(to_wsl_path_str(aimms_exe_path))
	if not exe_path_native.exists() and not exe_path_alt.exists():
		raise FileNotFoundError(f"AIMMS not found: {aimms_exe_path}")
	proj_path_native = Path(project_path)
	proj_path_alt = Path(to_wsl_path_str(project_path))
	if not proj_path_native.exists() and not proj_path_alt.exists():
		raise FileNotFoundError(f".aimms project not found: {project_path}")

	# Common batch flags (may require adjustment per AIMMS version):
	# -B or -b : batch mode
	# -R or -r : run procedure
	# Prepare strings for the target shell (PowerShell expects Windows-style)
	exe_str = to_windows_path_str(str(exe_path_native if exe_path_native.exists() else exe_path_alt))
	proj_str = to_windows_path_str(str(proj_path_native if proj_path_native.exists() else proj_path_alt))

	cmd_core_tokens = [
		f'"{exe_str}"',
		"-B",
		"-R", f'"{procedure}"',
		f'"{proj_str}"',
	]

	# Decide PowerShell usage
	ps_default = os.name == "posix"  # in WSL, prefer PowerShell
	use_ps = ps_default if use_powershell is None else use_powershell

	# If using PowerShell (e.g., WSL), use call operator (&) to invoke the .exe correctly
	if use_ps and (Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists() or Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe".lower()).exists()):
		ps_exe = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
		ps_cmd = "& " + " ".join(cmd_core_tokens)
		full_cmd = [ps_exe, "-NoProfile", "-Command", ps_cmd]
	else:
		# On native Windows Python, we can pass the args list directly
		full_cmd = [exe_str, "-B", "-R", procedure, proj_str]

	return subprocess.run(
		full_cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		encoding="latin-1",   # AIMMS/PowerShell often uses CP1252/Latin-1
		errors="replace"      # avoid decoding failures
	)


def main() -> None:
	"""
	Main application entry point for the Streamlit interface.
	
	This function orchestrates the entire user interface and workflow:
	1. Configuration section (sidebar)
	2. Model parameters input
	3. CSV file uploads
	4. Input validation and saving
	5. AIMMS model execution
	6. Results visualization and download
	
	The function uses Streamlit's session state to persist information
	between interactions and provides a comprehensive interface for
	all aspects of the optimization workflow.
	"""
	# ====================================================================
	# INITIALIZATION
	# ====================================================================
	st.set_page_config(page_title="AIMMS Interface (Streamlit)", layout="wide")
	ensure_dirs()
	cfg = load_config()

	# ====================================================================
	# PAGE HEADER
	# ====================================================================
	st.title("AIMMS Model Interface")
	st.caption("Set parameters, upload inputs, and run the model.")

	# ====================================================================
	# CONFIGURATION SECTION (Sidebar)
	# ====================================================================
	with st.sidebar:
		st.header("Configuration")
		aimms_exe_path = st.text_input(
			"AIMMS executable (Windows)",
			value=cfg.get("aimms_exe_path", ""),
			help="Example: C:\\Program Files\\AIMMS\\AIMMS 4.92\\bin\\AIMMS.exe"
		)
		project_path = st.text_input(
			"AIMMS project (.aimms)",
			value=cfg.get("project_path", ""),
		)
		run_procedure = st.text_input(
			"Procedure to execute",
			value=cfg.get("run_procedure", "RunOptimizationFromCSV"),
			help="Recommended: RunOptimizationFromCSV (reads inputs, runs the model, exports results)"
		)
		use_ps_flag = st.checkbox(
			"Use PowerShell (recommended on WSL)",
			value=bool(cfg.get("use_powershell", True))
		)
		st.markdown("---")
		st.write("Execution directories:")
		st.code(f"Input:  {RUNTIME_INPUT_DIR}\nOutput: {RUNTIME_OUTPUT_DIR}")

	# ====================================================================
	# MODEL PARAMETERS SECTION
	# ====================================================================
	st.header("Model parameters")
	col1, col2, col3 = st.columns(3)
	with col1:
		plan_horizon = st.number_input("Planning horizon (days) [PlanHorizon]", min_value=1, value=111, step=1, help="Index t ∈ Days = {1..PlanHorizon}")
		number_physicians = st.number_input("Number of physicians [NumberOfPhysicians]", min_value=1, value=10, step=1, help="Index j ∈ Physicians = {1..NumberOfPhysicians}")
		total_scenarios = st.number_input("Number of scenarios [TotalScenarios]", min_value=1, value=10, step=1, help="Index s ∈ Scenarios = {1..TotalScenarios}. The number of scenarios should be defined beforehand using the SAA (Sample Average Approximation) methodology according to the paper. For new application contexts, seek to understand the need to redefine the default value")
	with col2:
		min_or = st.number_input("Minimum ORs per day [MinOR]", min_value=0, value=1, step=1, help="Lower bound on daily OR usage in late period")
		max_consecutive_or = st.number_input("Max consecutive OR days [MaxConsecutiveORDays]", min_value=1, value=3, step=1, help="Upper bound of consecutive OR days per physician")
		begin_change_adm_pto = st.number_input("Admin/PTO window start day [BeginChangeAdmPTO]", min_value=1, value=84, step=1, help="Start day for Admin/PTO minimum checks")
		end_change_adm_pto = st.number_input("Admin/PTO window end day [EndChangeAdmPTO]", min_value=int(begin_change_adm_pto), value=int(plan_horizon), step=1, help="End day for Admin/PTO minimum checks (default: PlanHorizon)")
	with col3:
		alpha = st.number_input("Alpha [Alpha]", min_value=0.0, max_value=1.0, value=0.68, step=0.01, help="Proportion of clinic patients that will require surgery")
		beta = st.number_input("Beta [Beta]", min_value=0.0, max_value=1.0, value=0.8, step=0.05, help="Prioritization factor of the objective function")
		two_or_start = st.number_input("2OR window start [TwoORWindowStart]", min_value=1, value=45, step=1, help="First day allowing 2OR usage")
		two_or_end = st.number_input("2OR window end [TwoORWindowEnd]", min_value=int(two_or_start), value=68, step=1, help="Last day allowing 2OR usage")

	# ====================================================================
	# INPUT DATA SECTION (CSV Uploads)
	# ====================================================================
	st.header("Input data (CSV)")
	st.caption("Model indices as integer columns plus a 'value' column. Templates live in streamlit_app/templates.")
	with st.expander("Data dictionary and model legend", expanded=False):
		st.markdown(
			"### Indices\n"
			"- **j (Physician, p in paper)**: 1..NumberOfPhysicians. Decision-maker resources to be assigned daily.\n"
			"- **t (Day)**: 1..PlanHorizon. Discrete planning days in the horizon.\n"
			"- **s (Scenario, ξ in paper)**: 1..TotalScenarios. Demand uncertainty scenarios (SAA/Monte Carlo).\n"
			"- **k (Assignment type, K in paper)**: categorical location/task code per day:\n"
			"  - 1=OR (Operating room), 2=Clinic, 3=A/R/E (Admin/Research/Education), 4=Gonda, 5=2OR, 6=ER, 7=Unassign.\n"
			"\n"
			"### Sets and subsets (paper ↔ model)\n"
			"- **Physicians (P)** ↔ [Physicians]\n"
			"- **Days (T)** ↔ [Days]\n"
			"- **Assignments Type (K)** ↔ [Assign]\n"
			"- **Scenarios (Ξ)** ↔ [Scenarios]\n"
			"- **Days other tasks (τ)** ↔ [DaysOtherTasks] (alias of [Days2])\n"
			"- **OR (O)** ↔ [Assign_OR] = {k∈K | k∈{1,5}} (implemented via [isOR(k)]).\n"
			"- **Clinic (C)** ↔ [Assign_Clinic] = {k∈K | k=2} (implemented via [isClinic(k)]).\n"
			"- **Clinic and OR (CO)** ↔ [Assign_ClinicOR] = {k∈K | k∈{1,2,5}} (implemented via [isCO(k)]).\n"
			"- Flags [isOR/isClinic/isCO] are derived from AssignmentTypes.csv; if absent, defaults are OR={1,5}, Clinic={2}, CO={1,2,5}.\n"
			"\n"
			"### Main decision variables (AIMMS names)\n"
			"- **x(j,t,k)** [x]: Binary; 1 if physician j is assigned to task k on day t (first-stage).\n"
			"- **clinic(j,t,s)** [clinic]: Integer; number of clinic appointments performed by j on day t under scenario s.\n"
			"- **surgery(j,t,s)** [surgery]: Integer; number of surgeries performed by j on day t under scenario s.\n"
			"- **surgerydemand(j,t,s)** [surgerydemand]: Integer; surgery demand estimated from clinic.\n"
			"- **clinicwait(t,s)** [clinicwait]: Nonnegative; clinic queue length at day t, scenario s.\n"
			"- **surgerywait(j,t,s)** [surgerywait]: Nonnegative; surgery queue length for j at day t, scenario s.\n"
			"\n"
			"### Key input parameters (AIMMS names)\n"
			"- **Demand(t,s)** [Demand]: Patient arrivals into clinic on day t, scenario s.\n"
			"- **InicialQueueClinic** [InicialQueueClinic]: Initial clinic queue (scalar).\n"
			"- **InicialQueueSurgery(j)** [InicialQueueSurgery]: Initial surgery queue per physician.\n"
			"- **Clinic capacity(j,t)** [ClinicCapacity]: Maximum clinic appointments j can perform on day t.\n"
			"- **Surgery capacity(j,t,k)** [SurgeryCapacity]: OR capacity when k∈{1,5} for j,t (ignored otherwise).\n"
			"- **Assignment mask(j,t,k)** [PhysicianAssign]: 1 if j is allowed to take task k on day t; 0 otherwise.\n"
			"- **Max OR/day(t)** [MaxOR]: Max OR usage per day (weighted by [ORMultiplier(k)] for OR types).\n"
			"- **OR weight(k)** [ORMultiplier]: Weight for each OR assignment type k (e.g., 2OR=2).\n"
			"- **Min by type per physician (j,k)** [MinTypePerDay]: Minimum total assignments over the Admin/PTO window per physician and type; excludes Clinic/OR.\n"
			"- **Min OR/day** [MinOR]: Minimum number of ORs to use per eligible day block.\n"
			"- **Max consecutive OR days** [MaxConsecutiveORDays]: Upper bound on consecutive OR days per physician.\n"
			"- **BeginChangeAdmPTO / EndChangeAdmPTO**: Start/end day of the window for MinTypePerDay counting.\n"
			"- **TwoORWindowStart / TwoORWindowEnd**: Start/end day where 2OR is allowed (for defaults and reference).\n"
			"- **Alpha** [Alpha]: Proportion of clinic patients that will require surgery.\n"
			"- **Beta** [Beta]: Prioritization factor of the objective function.\n"
			"- **TwoWeekPattern(t)**: Binary indicator for aggregation/reporting only (optional; does not affect optimization).\n"
			"\n"
			"\n"
			
		)
	up1, up2, up3 = st.columns(3)
	with up1:
		demand_csv = st.file_uploader("Patient demand by day/scenario [Demand: t,s,value]", type=["csv"], help="Daily patient arrivals; per scenario")
		clinic_cap_csv = st.file_uploader("Clinic capacity per physician/day [ClinicCapacity: j,t,value]", type=["csv"], help="Max clinic patients per physician per day")
		inicial_queue_clinic = st.number_input("Initial clinic queue [InicialQueueClinic]", min_value=0, value=13, step=1, help="Scalar initial clinic queue")
	with up2:
		surgery_cap_csv = st.file_uploader("Surgery capacity per physician/day/type [SurgeryCapacity: j,t,k,value]", type=["csv"], help="OR capacity for k in {1,5}. If not provided, the example file in streamlit_app/example_data/SurgeryCapacity.csv will be used and no default will be generated.")
		inicial_queue_surg_csv = st.file_uploader("Initial surgery queue per physician [InicialQueueSurgery: j,value]", type=["csv"])
	with up3:
		max_or_csv = st.file_uploader("Max OR usage per day [MaxOR: t,value]", type=["csv"], help="If not provided, the example file in streamlit_app/example_data/MaxOR.csv will be used and no default will be generated.")
		phys_assign2_csv = st.file_uploader("Assignment mask (allowed=1) [PhysicianAssign: j,t,k,value]", type=["csv"], help="Set 1 when assignment j,t,k is allowed. If not provided, the example file in streamlit_app/example_data/PhysicianAssign.csv will be used and no default allocation will be generated.")
		twoweek_pattern_csv = st.file_uploader("Two-week pattern (binary) [TwoWeekPattern: t,value]", type=["csv"], help="Optional; used only for aggregation/reporting. Does not impact optimization. If not provided, the example file in streamlit_app/example_data/TwoWeekPattern.csv is used; otherwise defaults to zeros.")
	# Assign.csv uploader removed: Assign is derived from AssignmentTypes.csv or defaults
	
	aux1, aux2 = st.columns(2)
	with aux1:
		ormult_csv = st.file_uploader("OR weight per assignment type [ORMultiplier: k,value]", type=["csv"], help="Weight for OR usage (e.g., 2 for 2OR). Defaults: k=1→1, k=5→2.")
	with aux2:
		min_type_csv = st.file_uploader("Minimum by type per physician (window) [MinTypePerDay: j,k,value]", type=["csv"], help="Minimum total assignments per physician and type k over the Admin/PTO window; excludes Clinic/OR. If not provided, the example file in streamlit_app/example_data/MinTypePerDay.csv will be used and no default will be generated.")
	
	st.subheader("Assignments taxonomy and day subsets (optional)")
	tax1, tax2 = st.columns(2)
	with tax1:
		assignment_types_csv = st.file_uploader("AssignmentTypes.csv (k,name,category,include_in_CO)", type=["csv"], help="Defines categories; used to derive isOR/isClinic/isCO")
	with tax2:
		day_subsets_csv = st.file_uploader("DaySubsets.csv (subset,t) with subset='tau'", type=["csv"], help="Optional; defines τ (DaysOtherTasks) via dayInTau(t)=1 for analysis. Does not impact optimization. If absent, the app sets τ = {BeginChangeAdmPTO..EndChangeAdmPTO}.")
	st.caption("Note: TwoWeekPattern and Day Subsets (τ) are optional and used only for analysis/reporting; they do not affect the optimization.")
	# Safety option: preserve example data unless explicitly completing PhysicianAssign2
	ensure_assign_coverage = st.checkbox(
		"Ensure coverage (j,t) by adding Unassign when missing in PhysicianAssign",
		value=False,
		help="When enabled, if any pair (j,t) has no permission in PhysicianAssign, the app adds k=7 (Unassign) to avoid empty rows in the model. Disabled preserves the example data."
	)

	# ====================================================================
	# SAVE INPUTS SECTION
	# ====================================================================
	if st.button("Save inputs to runtime folder"):
		ok = True
		# Scalars
		save_scalar_to_csv("PlanHorizon", int(plan_horizon))
		save_scalar_to_csv("NumberOfPhysicians", int(number_physicians))
		save_scalar_to_csv("TotalScenarios", int(total_scenarios))
		save_scalar_to_csv("MinOR", int(min_or))
		save_scalar_to_csv("MaxConsecutiveORDays", int(max_consecutive_or))
		save_scalar_to_csv("BeginChangeAdmPTO", int(begin_change_adm_pto))
		save_scalar_to_csv("EndChangeAdmPTO", int(end_change_adm_pto))
		save_scalar_to_csv("TwoORWindowStart", int(two_or_start))
		save_scalar_to_csv("TwoORWindowEnd", int(two_or_end))
		save_scalar_to_csv("Alpha", float(alpha))
		save_scalar_to_csv("Beta", float(beta))
		save_scalar_to_csv("InicialQueueClinic", int(inicial_queue_clinic))

		# CSVs
		csv_saved: dict[str, Path] = {}
		if demand_csv and not save_uploaded_csv(demand_csv, "Demand.csv"):
			ok = False
		else:
			if demand_csv:
				csv_saved["Demand"] = RUNTIME_INPUT_DIR / "Demand.csv"
		if clinic_cap_csv and not save_uploaded_csv(clinic_cap_csv, "ClinicCapacity.csv"):
			ok = False
		else:
			if clinic_cap_csv:
				csv_saved["ClinicCapacity"] = RUNTIME_INPUT_DIR / "ClinicCapacity.csv"
		if surgery_cap_csv and not save_uploaded_csv(surgery_cap_csv, "SurgeryCapacity.csv"):
			ok = False
		else:
			if surgery_cap_csv:
				csv_saved["SurgeryCapacity"] = RUNTIME_INPUT_DIR / "SurgeryCapacity.csv"
		# Prefer example data for SurgeryCapacity when no upload is provided
		used_example_surgcap = False
		orig_surgcap_path = APP_ROOT / "example_data" / "SurgeryCapacity.csv"
		dest_surgcap_path = RUNTIME_INPUT_DIR / "SurgeryCapacity.csv"
		if "SurgeryCapacity" not in csv_saved and orig_surgcap_path.exists():
			try:
				shutil.copyfile(orig_surgcap_path, dest_surgcap_path)
				csv_saved["SurgeryCapacity"] = dest_surgcap_path
				used_example_surgcap = True
			except Exception:
				used_example_surgcap = False
		st.session_state["used_example_surgerycapacity"] = used_example_surgcap
		if inicial_queue_surg_csv and not save_uploaded_csv(inicial_queue_surg_csv, "InicialQueueSurgery.csv"):
			ok = False
		else:
			if inicial_queue_surg_csv:
				csv_saved["InicialQueueSurgery"] = RUNTIME_INPUT_DIR / "InicialQueueSurgery.csv"
		if max_or_csv and not save_uploaded_csv(max_or_csv, "MaxOR.csv"):
			ok = False
		else:
			if max_or_csv:
				csv_saved["MaxOR"] = RUNTIME_INPUT_DIR / "MaxOR.csv"
		# Prefer example data for MaxOR when no upload is provided
		used_example_maxor = False
		orig_maxor_path = APP_ROOT / "example_data" / "MaxOR.csv"
		dest_maxor_path = RUNTIME_INPUT_DIR / "MaxOR.csv"
		if "MaxOR" not in csv_saved and orig_maxor_path.exists():
			try:
				shutil.copyfile(orig_maxor_path, dest_maxor_path)
				csv_saved["MaxOR"] = dest_maxor_path
				used_example_maxor = True
			except Exception:
				used_example_maxor = False
		st.session_state["used_example_maxor"] = used_example_maxor
		if phys_assign2_csv and not save_uploaded_csv(phys_assign2_csv, "PhysicianAssign2.csv"):
			ok = False
		else:
			if phys_assign2_csv:
				csv_saved["PhysicianAssign2"] = RUNTIME_INPUT_DIR / "PhysicianAssign2.csv"
		# Prefer example data for PhysicianAssign when no upload is provided
		used_example_assign = False
		orig_assign_path = APP_ROOT / "example_data" / "PhysicianAssign.csv"
		dest_assign2_path = RUNTIME_INPUT_DIR / "PhysicianAssign2.csv"
		if "PhysicianAssign2" not in csv_saved and orig_assign_path.exists():
			try:
				shutil.copyfile(orig_assign_path, dest_assign2_path)
				csv_saved["PhysicianAssign2"] = dest_assign2_path
				used_example_assign = True
			except Exception:
				used_example_assign = False
		st.session_state["used_example_physicianassign"] = used_example_assign
		# No direct Assign.csv upload; Assign will be created from AssignmentTypes.csv (or defaults)
		if twoweek_pattern_csv and not save_uploaded_csv(twoweek_pattern_csv, "TwoWeekPattern.csv"):
			ok = False
		else:
			if twoweek_pattern_csv:
				csv_saved["TwoWeekPattern"] = RUNTIME_INPUT_DIR / "TwoWeekPattern.csv"
		# Prefer example data for TwoWeekPattern when no upload is provided
		used_example_twoweekpattern = False
		orig_twoweek_path = APP_ROOT / "example_data" / "TwoWeekPattern.csv"
		dest_twoweek_path = RUNTIME_INPUT_DIR / "TwoWeekPattern.csv"
		if "TwoWeekPattern" not in csv_saved and orig_twoweek_path.exists():
			try:
				shutil.copyfile(orig_twoweek_path, dest_twoweek_path)
				csv_saved["TwoWeekPattern"] = dest_twoweek_path
				used_example_twoweekpattern = True
			except Exception:
				used_example_twoweekpattern = False
		st.session_state["used_example_twoweekpattern"] = used_example_twoweekpattern
		# Prefer example data for MinTypePerDay when no upload is provided (before generating template)
		used_example_mintypeperday = False
		orig_mintype_path = APP_ROOT / "example_data" / "MinTypePerDay.csv"
		dest_mintype_path = RUNTIME_INPUT_DIR / "MinTypePerDay.csv"
		if "MinTypePerDay" not in csv_saved and orig_mintype_path.exists():
			try:
				shutil.copyfile(orig_mintype_path, dest_mintype_path)
				csv_saved["MinTypePerDay"] = dest_mintype_path
				used_example_mintypeperday = True
			except Exception:
				used_example_mintypeperday = False
		st.session_state["used_example_mintypeperday"] = used_example_mintypeperday

		if ok:
			# ============================================================
			# Fill missing CSVs from templates or generate defaults
			# ============================================================
			fallback_used = []
			for key in ["Assign", "Demand", "ClinicCapacity", "SurgeryCapacity", "PhysicianAssign2", "MaxOR", "TwoWeekPattern", "InicialQueueSurgery", "MinARE", "ORMultiplier", "MinTypePerDay", "isOR", "isClinic", "isCO", "dayInTau"]:
				dest = RUNTIME_INPUT_DIR / f"{key}.csv"
				if not dest.exists():
					if ensure_template_if_missing(key, dest, int(plan_horizon), int(total_scenarios), int(number_physicians)):
						fallback_used.append(key)
						csv_saved[key] = dest
			# Persist info to also notify in the Execution section
			st.session_state["fallback_used"] = fallback_used
			# Also repopulate templates directory if it's empty
			created_templates = populate_templates_dir(int(plan_horizon), int(total_scenarios), int(number_physicians))
			
			# ============================================================
			# Normalization: ensure coverage (j,t) in PhysicianAssign2
			# ============================================================
			def ensure_physassign2_coverage(path: Path, num_phys: int, horizon: int) -> None:
				try:
					df = pd.read_csv(path)
					# Rename common columns
					col_map = {}
					for c in df.columns:
						lc = str(c).strip().lower()
						if lc in ("t","j","k","s"):
							col_map[c] = lc
						elif lc in ("valor","value","val"):
							col_map[c] = "value"
					if col_map:
						df = df.rename(columns=col_map)
					required = {"j","t","k","value"}
					if not required.issubset(df.columns):
						return
					# Coerce types
					for c in ("j","t","k","value"):
						df[c] = pd.to_numeric(df[c], errors="coerce")
					df = df.dropna(subset=["j","t","k","value"])
					df[["j","t","k","value"]] = df[["j","t","k","value"]].astype(int)
					# Consider only active masks (value==1)
					df = df[df["value"] == 1]
					covered = set((int(row.j), int(row.t)) for _, row in df.iterrows())
					missing_rows = []
					for j in range(1, num_phys + 1):
						for t in range(1, horizon + 1):
							if (j, t) not in covered:
								# Permissive fallback: allow Unassign (k=7)
								missing_rows.append({"j": j, "t": t, "k": 7, "value": 1})
					if missing_rows:
						df2 = pd.concat([df, pd.DataFrame(missing_rows)], ignore_index=True)
						df2.to_csv(path, index=False)
				except Exception:
					# Silent: if it fails, continue with the example file
					pass
			
			# Apply normalization if PhysicianAssign2 exists and the option is checked
			if ensure_assign_coverage and "PhysicianAssign2" in csv_saved and csv_saved["PhysicianAssign2"].exists():
				ensure_physassign2_coverage(csv_saved["PhysicianAssign2"], int(number_physicians), int(plan_horizon))
			elif "PhysicianAssign2" in csv_saved and csv_saved["PhysicianAssign2"].exists():
				# If not completing automatically, alert if there are (j,t) pairs without any permission
				try:
					df_pa = pd.read_csv(csv_saved["PhysicianAssign2"])
					col_map = {}
					for c in df_pa.columns:
						lc = str(c).strip().lower()
						if lc in ("t","j","k","s"):
							col_map[c] = lc
						elif lc in ("valor","value","val"):
							col_map[c] = "value"
					if col_map:
						df_pa = df_pa.rename(columns=col_map)
					if {"j","t","k","value"}.issubset(df_pa.columns):
						df_pa = df_pa[df_pa["value"] == 1]
						covered = set((int(j), int(t)) for j,t in zip(pd.to_numeric(df_pa["j"], errors="coerce"), pd.to_numeric(df_pa["t"], errors="coerce")) if pd.notna(j) and pd.notna(t))
						missing = []
						for j in range(1, int(number_physicians) + 1):
							for t in range(1, int(plan_horizon) + 1):
								if (j,t) not in covered:
									missing.append((j,t))
						if missing:
							preview = ", ".join([f"(j={j},t={t})" for j,t in missing[:10]])
							rest = f" and {len(missing)-10} more" if len(missing) > 10 else ""
							st.warning(f"PhysicianAssign contains no permission for {len(missing)} pairs (j,t). Examples: {preview}{rest}. This may generate empty rows in constraints. Check the coverage option with Unassign to avoid this.")
				except Exception:
					pass
			
			# ============================================================
			# Process taxonomy to create isOR/isClinic/isCO CSVs
			# ============================================================
			def produce_flags_from_assignment_types(path: Path) -> tuple[list[int], list[int], list[int]]:
				try:
					df = pd.read_csv(path)
					def norm(s): 
						return [] if pd.isna(s) else [t.strip().lower() for t in str(s).split(";")]
					or_set, clinic_set, co_set = set(), set(), set()
					all_k = set()
					for _, row in df.iterrows():
						k = int(row["k"])
						all_k.add(k)
						name = str(row.get("name", "")).strip().lower()
						cats = norm(row.get("category", ""))
						inc_co = int(row.get("include_in_CO", 0))
						if "or" in cats or name in ("or","2or"):
							or_set.add(k)
						if "clinic" in cats or name == "clinic":
							clinic_set.add(k)
						if inc_co == 1 or k in or_set or k in clinic_set:
							co_set.add(k)
					return sorted(or_set), sorted(clinic_set), sorted(co_set), sorted(all_k)
				except Exception:
					return [], [], [], []
			
			def write_flag_csv(filename: str, keys: list[int], col: str):
				df = pd.DataFrame([{col: k, "value": 1} for k in keys])
				(RUNTIME_INPUT_DIR / filename).parent.mkdir(parents=True, exist_ok=True)
				df.to_csv(RUNTIME_INPUT_DIR / filename, index=False)
			
			# If user provided AssignmentTypes.csv, derive flags; otherwise generate defaults
			if assignment_types_csv:
				at_path = RUNTIME_INPUT_DIR / "AssignmentTypes.csv"
				pd.read_csv(assignment_types_csv).to_csv(at_path, index=False)
				or_list, clinic_list, co_list, all_k_list = produce_flags_from_assignment_types(at_path)
				if not or_list and not clinic_list and not co_list:
					# fallback to defaults
					or_list, clinic_list, co_list, all_k_list = [1,5], [2], [1,2,5], [1,2,3,4,5,6,7]
				write_flag_csv("isOR.csv", or_list, "k")
				write_flag_csv("isClinic.csv", clinic_list, "k")
				write_flag_csv("isCO.csv", co_list, "k")
				# Assign universe from AssignmentTypes
				df_assign = pd.DataFrame([{"k": k} for k in all_k_list])
				df_assign.to_csv(RUNTIME_INPUT_DIR / "Assign.csv", index=False)
				csv_saved["Assign"] = RUNTIME_INPUT_DIR / "Assign.csv"
				csv_saved["isOR"] = RUNTIME_INPUT_DIR / "isOR.csv"
				csv_saved["isClinic"] = RUNTIME_INPUT_DIR / "isClinic.csv"
				csv_saved["isCO"] = RUNTIME_INPUT_DIR / "isCO.csv"
			else:
				# defaults or template generation if missing
				for flag_name, keys in [("isOR",[1,5]), ("isClinic",[2]), ("isCO",[1,2,5])]:
					dest = RUNTIME_INPUT_DIR / f"{flag_name}.csv"
					if not dest.exists():
						write_flag_csv(f"{flag_name}.csv", keys, "k")
					csv_saved[flag_name] = dest
				# Ensure Assign exists
				dest_assign = RUNTIME_INPUT_DIR / "Assign.csv"
				if not dest_assign.exists():
					pd.DataFrame([{"k": k} for k in [1,2,3,4,5,6,7]]).to_csv(dest_assign, index=False)
				csv_saved["Assign"] = dest_assign
			
			# ============================================================
			# Handle optional direct uploads for ORMultiplier and MinTypePerDay
			# ============================================================
			if ormult_csv and not save_uploaded_csv(ormult_csv, "ORMultiplier.csv"):
				ok = False
			else:
				if (RUNTIME_INPUT_DIR / "ORMultiplier.csv").exists():
					csv_saved["ORMultiplier"] = RUNTIME_INPUT_DIR / "ORMultiplier.csv"
			if min_type_csv and not save_uploaded_csv(min_type_csv, "MinTypePerDay.csv"):
				ok = False
			else:
				if (RUNTIME_INPUT_DIR / "MinTypePerDay.csv").exists():
					csv_saved["MinTypePerDay"] = RUNTIME_INPUT_DIR / "MinTypePerDay.csv"
			# If MinTypePerDay was uploaded, override the example usage indicator
			if min_type_csv:
				st.session_state["used_example_mintypeperday"] = False
			
			# ============================================================
			# Process day subsets for tau (DaysOtherTasks)
			# ============================================================
			def produce_dayintau_from_csv(path: Path) -> list[int]:
				try:
					df = pd.read_csv(path)
					df = df[df["subset"].astype(str).str.lower().eq("tau")]
					return sorted(pd.to_numeric(df["t"], errors="coerce").dropna().astype(int).tolist())
				except Exception:
					return []
			if day_subsets_csv:
				ds_path = RUNTIME_INPUT_DIR / "DaySubsets.csv"
				pd.read_csv(day_subsets_csv).to_csv(ds_path, index=False)
				tau_days = produce_dayintau_from_csv(ds_path)
				df_tau = pd.DataFrame([{"t": t, "value": 1} for t in tau_days])
				df_tau.to_csv(RUNTIME_INPUT_DIR / "dayInTau.csv", index=False)
				csv_saved["dayInTau"] = RUNTIME_INPUT_DIR / "dayInTau.csv"
			else:
				# default: tau mirrors legacy Days2 window → use [BeginChangeAdmPTO .. PlanHorizon]
				tau_days = list(range(int(begin_change_adm_pto), int(end_change_adm_pto) + 1))
				df_tau = pd.DataFrame([{"t": t, "value": 1} for t in tau_days])
				df_tau.to_csv(RUNTIME_INPUT_DIR / "dayInTau.csv", index=False)
				csv_saved["dayInTau"] = RUNTIME_INPUT_DIR / "dayInTau.csv"

			# ============================================================
			# Validate PlanHorizon and TotalScenarios coverage
			# ============================================================
			max_t_csv = compute_max_t_from_csvs({k: RUNTIME_INPUT_DIR / f"{k}.csv" for k in [
				"Demand", "ClinicCapacity", "SurgeryCapacity", "PhysicianAssign2", "MaxOR", "TwoWeekPattern"
			]})
			max_s_csv = compute_max_s_from_demand(RUNTIME_INPUT_DIR / "Demand.csv")

			# ============================================================
			# Compose AIMMS text input file aggregating all inputs
			# ============================================================
			scalars = {
				"PlanHorizon": int(plan_horizon),
				"NumberOfPhysicians": int(number_physicians),
				"TotalScenarios": int(total_scenarios),
				"MinOR": int(min_or),
				"MaxConsecutiveORDays": int(max_consecutive_or),
				"BeginChangeAdmPTO": int(begin_change_adm_pto),
				"EndChangeAdmPTO": int(end_change_adm_pto),
				"TwoORWindowStart": int(two_or_start),
				"TwoORWindowEnd": int(two_or_end),
				"Alpha": float(alpha),
				"Beta": float(beta),
				"InicialQueueClinic": int(inicial_queue_clinic),
			}
			aimms_txt = build_and_write_aimms_input_text(scalars, csv_saved)
			st.success("Inputs saved to runtime_input/.")
			st.caption(f"Generated AIMMS consolidated text file: {aimms_txt.name}")
			if fallback_used:
				st.info("Templates used for: " + ", ".join(sorted(fallback_used)))
			if created_templates:
				st.info("Templates created in templates/: " + ", ".join(sorted(created_templates)))
			if st.session_state.get("used_example_physicianassign"):
				st.info("Using PhysicianAssign from the example problem (streamlit_app/example_data/PhysicianAssign.csv). Since no upload was provided, the example data was used and no default allocation was generated.")
			if st.session_state.get("used_example_surgerycapacity"):
				st.info("Using SurgeryCapacity from the example problem (streamlit_app/example_data/SurgeryCapacity.csv). Since no upload was provided, the example data was used and no default capacity was generated.")
			if st.session_state.get("used_example_maxor"):
				st.info("Using MaxOR from the example problem (streamlit_app/example_data/MaxOR.csv). Since no upload was provided, the example data was used and no default was generated.")
			if st.session_state.get("used_example_twoweekpattern"):
				st.info("Using TwoWeekPattern from the example problem (streamlit_app/example_data/TwoWeekPattern.csv). Since no upload was provided, the example data was used and no default pattern was generated.")
			if st.session_state.get("used_example_mintypeperday"):
				st.info("Using MinTypePerDay from the example problem (streamlit_app/example_data/MinTypePerDay.csv). Since no upload was provided, the example data was used and no default was generated.")
			if max_t_csv is not None and int(plan_horizon) > max_t_csv:
				st.warning(f"PlanHorizon ({int(plan_horizon)}) is greater than max t in CSVs ({max_t_csv}). "
				           f"Consider raising CSV coverage up to t={int(plan_horizon)} or reducing the horizon.")
			elif max_t_csv is not None and int(plan_horizon) < max_t_csv:
				st.info(f"CSVs contain t up to {max_t_csv}, while PlanHorizon is {int(plan_horizon)}. "
				        f"The model will only use up to t={int(plan_horizon)}.")
			if max_s_csv is not None and int(total_scenarios) < max_s_csv:
				st.warning(f"TotalScenarios ({int(total_scenarios)}) is less than max s in Demand ({max_s_csv}). "
				           f"Consider adjusting TotalScenarios or trimming Demand.")
			elif max_s_csv is not None and int(total_scenarios) > max_s_csv:
				st.info(f"Demand has s up to {max_s_csv}, while TotalScenarios is {int(total_scenarios)}. "
				        f"The model will only use up to s={max_s_csv}.")
		else:
			st.warning("Some files were not saved. Check the messages above.")

	# ====================================================================
	# RUN MODEL SECTION
	# ====================================================================
	st.header("Run")
	st.caption("Run the AIMMS procedure (set the AIMMS executable and project paths).")
	st.info("⚠️ **Note:** The model may take several minutes to complete execution.")
	# Additional notice if templates are being used
	if "fallback_used" in st.session_state and st.session_state["fallback_used"]:
		st.info("Templates will be used for: " + ", ".join(sorted(st.session_state["fallback_used"])))
	if st.session_state.get("used_example_physicianassign"):
		st.info("Notice: Using PhysicianAssign from the example problem (streamlit_app/example_data/PhysicianAssign.csv). No default allocation was generated.")
	if st.session_state.get("used_example_surgerycapacity"):
		st.info("Notice: Using SurgeryCapacity from the example problem (streamlit_app/example_data/SurgeryCapacity.csv). No default capacity was generated.")
	if st.session_state.get("used_example_maxor"):
		st.info("Notice: Using MaxOR from the example problem (streamlit_app/example_data/MaxOR.csv). No default MaxOR was generated.")
	if st.session_state.get("used_example_twoweekpattern"):
		st.info("Notice: Using TwoWeekPattern from the example problem (streamlit_app/example_data/TwoWeekPattern.csv). No default pattern was generated.")
	if st.session_state.get("used_example_mintypeperday"):
		st.info("Notice: Using MinTypePerDay from the example problem (streamlit_app/example_data/MinTypePerDay.csv). No default MinTypePerDay was generated.")
	if st.button("Run model in AIMMS"):
		try:
			res = run_aimms(
				aimms_exe_path=aimms_exe_path,
				project_path=project_path,
				procedure=run_procedure,
				use_powershell=use_ps_flag
			)
			st.subheader("Process output")
			with st.expander("View detailed output", expanded=False):
				tab_out, tab_err = st.tabs(["Stdout", "Stderr"])
				with tab_out:
					stdout_text = res.stdout or "—"
					st.code(stdout_text)
					if stdout_text and stdout_text != "—":
						st.download_button("Download stdout", stdout_text.encode("latin-1", errors="replace"), "stdout.txt")
				with tab_err:
					st.code(res.stderr or "—")
			if res.returncode != 0:
				st.error(f"Execution failed (exit code {res.returncode}).")
			else:
				st.success("Execution completed.")
		except Exception as e:
			st.error(f"Error executing AIMMS: {e}")

	# ====================================================================
	# RESULTS SECTION
	# ====================================================================
	st.header("Results")
	st.caption("Visualize optimization results and download data.")
	
	output_file = RUNTIME_OUTPUT_DIR / "aimms_output.txt"
	
	if output_file.exists():
		# Parse output
		parsed = parse_aimms_output(output_file)
		
		# Display objective function value
		if parsed['Z'] is not None:
			st.subheader("Objective Function")
			st.metric("Z (Objective Function Value)", f"{parsed['Z']:.4f}")
		else:
			st.warning("Objective function value not found in output file.")
		
		# Download original output file
		st.subheader("Download Original Output")
		with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
			output_content = f.read()
		st.download_button(
			label="Download AIMMS output (as is)",
			data=output_content,
			file_name="aimms_output.txt",
			mime="text/plain"
		)
		
		# Create CSVs section
		st.subheader("Export Variables to CSV")
		
		# Button to create/update CSVs
		if st.button("Create/Update Variable CSVs"):
			with st.spinner("Processing variables and creating CSVs..."):
				csv_paths = create_csvs_from_output(output_file, RUNTIME_OUTPUT_DIR)
				st.session_state['variable_csvs'] = csv_paths
				if csv_paths:
					st.success(f"CSVs created for {len(csv_paths)} variable(s).")
				else:
					st.warning("No variables found in output file.")
		
		# Display download buttons for each variable
		if 'variable_csvs' in st.session_state and st.session_state['variable_csvs']:
			st.markdown("#### Download CSVs by Variable")
			
			# Variables in the order specified by user
			variable_order = ['x', 'clinic', 'surgery', 'surgerydemand', 'clinicwait', 'surgerywait']
			
			cols = st.columns(3)
			for idx, var_name in enumerate(variable_order):
				if var_name in st.session_state['variable_csvs']:
					csv_path = st.session_state['variable_csvs'][var_name]
					with cols[idx % 3]:
						try:
							with open(csv_path, 'rb') as f:
								csv_data = f.read()
							st.download_button(
								label=f"📥 {var_name}.csv",
								data=csv_data,
								file_name=f"{var_name}.csv",
								mime="text/csv",
								key=f"download_{var_name}"
							)
						except Exception as e:
							st.error(f"Error reading {var_name}.csv: {e}")
			
			# Show summary
			with st.expander("Variable Summary", expanded=False):
				for var_name in variable_order:
					if var_name in st.session_state['variable_csvs']:
						csv_path = st.session_state['variable_csvs'][var_name]
						try:
							df = pd.read_csv(csv_path)
							st.write(f"**{var_name}**: {len(df)} records")
							if len(df) > 0:
								st.dataframe(df.head(10), use_container_width=True)
							else:
								st.write(f"*{var_name} is empty*")
						except Exception as e:
							st.error(f"Error reading {var_name}.csv: {e}")
		else:
			st.info("Click 'Create/Update Variable CSVs' to generate CSV files.")
	else:
		st.info("Run the model first to see results. The output file will be generated after execution.")

	# ====================================================================
	# PUBLICATION SECTION
	# ====================================================================
	st.header("For publication (ESWA)")
	with st.expander("How to cite / Real-world system note", expanded=False):
		st.markdown(
			"- Real-world system statement: This application provides a working decision-support system connecting a stochastic scheduling optimization model (AIMMS) with a user-facing interface (Streamlit). It ingests operational data (capacities, assignments, demand), runs the optimization, and exports actionable schedules and KPIs.\n"
			"- Reproducibility: The app ships with complete input templates and default parameters allowing end-to-end runs.\n"
			"- Indices and entities are documented in the Data dictionary section above to ensure interpretability.\n"
			"- Authors:\n"
			"  > Janaina Figueira Marchesiᵃ, Silvio Hamacherᵃᵇ, Igor Tona Peresᵃ\n"
			"  > \n"
			"  > ᵃ Department of Industrial Engineering, Pontifical Catholic University of Rio de Janeiro (PUC-Rio), Rua Marquês de São Vicente, 225, Rio de Janeiro, 22451-040, Rio de Janeiro, Brazil\n"
			"  > \n"
			"  > Suggested citation entry (to adapt with title and year):\n"
			"  > Marchesi, J. F., Hamacher, S., & Peres, I. T. (Year). Title. Expert Systems with Applications.\n"
			"  > Include a link or repository DOI if available.\n"
		)


if __name__ == "__main__":
	main()


