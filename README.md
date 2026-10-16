# Streamlit UI

This app provides an interface to prepare inputs (CSV) and trigger the AIMMS optimization project.

## Structure

- `streamlit_app/app.py`: Streamlit application
- `streamlit_app/runtime_input/`: Directory where CSV files and scalars saved by the app are placed
- `streamlit_app/runtime_output/`: Directory where output files (CSV) exported by AIMMS should appear
- `streamlit_app/templates/`: CSV templates for preparing your data
- `streamlit_app/example_data/`: Example data files used as fallback when uploads are not provided
- `streamlit_app/config.yaml`: Local configuration (path to AIMMS.exe, .aimms project, procedure)

## Requirements

```bash
pip install -r streamlit_app/requirements.txt
```

## Running

```bash
streamlit run streamlit_app/app.py
```

Configure in the sidebar:
- AIMMS executable path (Windows)
- AIMMS project path (`.aimms` file, e.g., `model_aimms/CPLAN.aimms`)
  - Note: The app automatically detects the new `model_aimms` folder. If you haven't renamed the old folder yet (`CPLAN Stochastic SAA CERTO StoHard Cap8`), point to it until you complete the change.
- Procedure to execute (recommended default: `RunOptimizationFromCSV`)

Then:
- Use the forms to fill in scalars and upload your CSVs
- Click "Save inputs to runtime folder"
- Click "Run model in AIMMS"
  - Note: The model may take a few minutes to finish execution.

## Integration with AIMMS

For the app to work end-to-end, use the `RunOptimizationFromCSV` procedure in AIMMS. It:
1. Reads CSVs from `streamlit_app/runtime_input/` (or the consolidated file `aimms_input.txt`) and populates parameters/sets.
2. Executes the optimization model (e.g., `SolveCPLAN` or the mathematical program you configure).
3. Exports results (e.g., `x`, `clinicwait`, `surgerywait`, etc.) to `streamlit_app/runtime_output/`.

### Note on SAA Components

The AIMMS model in the `model_aimms` folder includes SAA (Sample Average Approximation) components that are available in the codebase. However, **the SAA functionality is out of scope for this Streamlit application**. The developed system is designed for day-to-day operational use in hospitals and clinics, where it is recommended that the model be used after conducting a preliminary SAA study to determine the appropriate number of scenarios and validate the solution quality for the specific application context. The SAA components remain available in the model for research and initial setup purposes, but are not integrated into the standard workflow of this application.

## Input File Examples

Example files expected by the app (see `templates/` and `example_data/`):

### Core Data Files
- `Demand.csv` (columns: `t,s,value`) - Patient arrivals by day and scenario
- `ClinicCapacity.csv` (columns: `j,t,value`) - Maximum clinic appointments per physician per day
- `SurgeryCapacity.csv` (columns: `j,t,k,value`) - OR capacity for assignment types k∈{1,5}
- `InicialQueueSurgery.csv` (columns: `j,value`) - Initial surgery queue per physician
- `InicialQueueClinic` - Scalar parameter for initial clinic queue
- `MaxOR.csv` (columns: `t,value`) - Maximum OR usage per day
- `PhysicianAssign2.csv` (columns: `j,t,k,value` 0/1) - Assignment mask (1=allowed, 0=not allowed)
- `TwoWeekPattern.csv` (columns: `t,value` 0/1) - Optional binary pattern for reporting/analysis

### Auxiliary Files
- `MinARE.csv` (columns: `j,value`) - Minimum ARE assignments per physician
- `ORMultiplier.csv` (columns: `k,value`) - OR weight per assignment type
- `MinTypePerDay.csv` (columns: `j,k,value`) - Minimum assignments per physician and type over Admin/PTO window

### Taxonomy and Subsets (Optional)
- `AssignmentTypes.csv` (columns: `k,name,category,include_in_CO`) - Defines assignment categories and derives isOR/isClinic/isCO flags
- `DaySubsets.csv` (columns: `subset,t`) - Defines day subsets (e.g., τ for DaysOtherTasks) when `subset='tau'`

### Generated Files
The app automatically generates:
- `Assign.csv` (columns: `k`) - Set of all assignment types
- `isOR.csv`, `isClinic.csv`, `isCO.csv` (columns: `k,value`) - Category membership flags
- `dayInTau.csv` (columns: `t,value`) - Days in the τ subset (defaults to [BeginChangeAdmPTO..EndChangeAdmPTO])

## Data Handling

- **Templates**: If a CSV is not uploaded, the app first checks for a template in `templates/`, then generates a default file.
- **Example Data**: For certain files (SurgeryCapacity, MaxOR, PhysicianAssign, TwoWeekPattern, MinTypePerDay), if no upload is provided, the app uses example files from `example_data/` before generating defaults.
- **Column Normalization**: The app accepts column name variations (e.g., `t`/`T`, `j`/`J`, `value`/`valor`/`VALOR`).
- **Validation**: The app validates PlanHorizon against max `t` in CSVs and TotalScenarios against max `s` in Demand.

## Notes on Windows/WSL Execution

- AIMMS is a Windows application. If running Streamlit on WSL, the app attempts to call `AIMMS.exe` using `powershell.exe`.
- Ensure that configured paths point to the correct executable and project files on Windows.
- The `use_powershell` option in the sidebar (recommended on WSL) enables PowerShell-based execution for better path handling.

## Results

After execution, results are available in the "Results" section:
- Objective function value (Z) is displayed
- Variable CSVs can be generated and downloaded (x, clinic, surgery, surgerydemand, clinicwait, surgerywait)
- Original AIMMS output file (`aimms_output.txt`) can be downloaded

## Additional Documentation

For detailed technical documentation, see `streamlit_app/DOCUMENTATION.md`.

### Nomenclature Reference

For a comprehensive mapping between the academic paper notation, AIMMS model parameters, and the Streamlit interface labels, see the [Nomenclature Mapping](#nomenclature-mapping) section in `streamlit_app/DOCUMENTATION.md`. This reference helps ensure consistency when working with the model across different contexts.

## Open-source implementation (no AIMMS required)

The model and the full experimental pipeline of the paper are also available as free
software, maintained at **[igor-peres/dsf-physician-assignment](https://github.com/igor-peres/dsf-physician-assignment)**:

- **Web app**: **https://igor-peres.github.io/dsf-physician-assignment/** runs in the browser and needs no install.
  Load the paper's case or your own division, run the pipeline (HiGHS solver compiled to
  WebAssembly) and compare with the values reported in the paper.
- **Python package** (`pip install -r requirements.txt`, then `python -m dsf data/crs_case.json`):
  same formulation, Eqs. (2)–(14), solved with [HiGHS](https://highs.dev) (MIT license).

What it covers, using the paper's section numbers:

| Paper | Open-source version |
|---|---|
| Two-stage stochastic model, Eqs. (2)–(14) | `dsf/model.py` |
| Poisson scenarios and SAA convergence (Sec. 3.5, 4.1.1) | `dsf/scenarios.py`, `dsf/saa.py` |
| Efficiency frontier over β, Table 5 | `python -m dsf case.json` |
| DES rule (Eq. 15), EV/EEV, WS, VSS, EVPI (Tables 6–7) | `dsf/baselines.py` |
| Observed vs optimized schedule (Table 8) | `--observed schedule.csv --observed-window 83,110` |
| Continuity-of-care relaxation (Table 10) | `--relax 0,0.2,0.5,1` |

The AIMMS project in `model_aimms/` and the Streamlit interface in `streamlit_app/` stay
the reference implementation used for the results in the paper. Because the open-source
version uses a different solver and a rolling-horizon strategy, its results can differ
slightly. Institutional inputs that were not published (minimum assignments per physician,
availability) have to be supplied to reproduce the paper's tables exactly.
