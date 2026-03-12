# CPLAN - AIMMS Model Interface Documentation

## Table of Contents

1. [Application Overview](#application-overview)
2. [Architecture](#architecture)
3. [Data Flow](#data-flow)
4. [Key Concepts](#key-concepts)
5. [Nomenclature Mapping](#nomenclature-mapping)
6. [Input File Formats](#input-file-formats)
7. [Output File Formats](#output-file-formats)
8. [Function Reference](#function-reference)
9. [Configuration](#configuration)
10. [Troubleshooting](#troubleshooting)
11. [Maintenance Guide](#maintenance-guide)

---

## Application Overview

The AIMMS Streamlit Interface is a web-based decision support system that provides a user-friendly interface for the optimization model implemented in AIMMS. The application bridges the gap between operational data (stored in CSV files) and the AIMMS optimization engine, making it accessible to users without deep technical knowledge of optimization modeling.

### Purpose

The application enables healthcare administrators and planners to:
- Configure optimization model parameters through an intuitive web interface
- Upload operational data (demand, capacities, assignments) in CSV format
- Execute the AIMMS optimization model with a single click
- Visualize and download optimization results for further analysis

### Main Components

1. **Streamlit Web Interface**: User-facing frontend for parameter configuration and data upload
2. **Data Transformation Layer**: Converts CSV files to AIMMS text format
3. **AIMMS Integration**: Executes the optimization model in batch mode
4. **Results Parser**: Extracts and visualizes optimization results
5. **Template System**: Provides default data files for quick testing

---

## Architecture

### Directory Structure

```
streamlit_app/
├── app.py                    # Main application file
├── config.yaml               # User configuration (paths, procedures)
├── templates/                # Default CSV templates
├── runtime_input/            # User-uploaded and generated input files
├── runtime_output/           # AIMMS output files and parsed results
├── original_data/            # Example data files (optional)
└── DOCUMENTATION.md          # This file
```

### Component Interaction

```
User Input (UI)
    ↓
CSV Files → Data Validation → AIMMS Text Format
    ↓
AIMMS Execution (Batch Mode)
    ↓
Output Text File → Parser → CSV Files → Visualization
```

### Key Technologies

- **Streamlit**: Web framework for Python applications
- **Pandas**: Data manipulation and CSV handling
- **AIMMS**: Optimization modeling platform
- **Python subprocess**: AIMMS execution interface

---

## Data Flow

### 1. Input Phase

1. User configures model parameters (planning horizon, number of physicians, etc.)
2. User uploads CSV files or uses templates/defaults
3. Application validates data and normalizes column names
4. Application generates missing files from templates or defaults
5. All inputs are consolidated into `aimms_input.txt`

### 2. Execution Phase

1. User clicks "Run model in AIMMS"
2. Application invokes AIMMS executable with batch flags
3. AIMMS reads `aimms_input.txt` and populates model parameters
4. AIMMS executes the optimization procedure
5. AIMMS writes results to `aimms_output.txt`

### 3. Output Phase

1. Application parses `aimms_output.txt`
2. Results are extracted into structured DataFrames
3. CSV files are generated for each variable
4. Results are displayed in the web interface
5. Users can download individual CSVs or a ZIP archive

---

## Key Concepts

### Model Indices

The optimization model uses four main indices:

- **j (Physician)**: Index for physicians/resources (1..NumberOfPhysicians)
  - Represents the decision-maker resources to be assigned daily
  - Corresponds to "p" in academic papers

- **t (Day)**: Index for time periods (1..PlanHorizon)
  - Represents discrete planning days in the horizon
  - Corresponds to "T" (Days set) in the model

- **s (Scenario)**: Index for uncertainty scenarios (1..TotalScenarios)
  - Represents demand uncertainty scenarios (SAA/Monte Carlo)
  - Corresponds to "Ξ" (Scenarios set) in the model

- **k (Assignment Type)**: Index for assignment categories
  - Categorical location/task code per day:
    - 1 = OR (Operating room)
    - 2 = Clinic
    - 3 = A/R/E (Admin/Research/Education)
    - 4 = Gonda
    - 5 = 2OR (Double OR)
    - 6 = ER (Emergency Room)
    - 7 = Unassign

### Sets and Subsets

The model defines several sets and subsets:

- **Physicians (P)**: Set of all physicians [Physicians]
- **Days (T)**: Set of all planning days [Days]
- **Assignments Type (K)**: Set of assignment types [Assign]
- **Scenarios (Ξ)**: Set of all scenarios [Scenarios]
- **Days other tasks (τ)**: Subset of days for other tasks [DaysOtherTasks]
- **OR (O)**: OR assignment types {1,5} [Assign_OR]
- **Clinic (C)**: Clinic assignment type {2} [Assign_Clinic]
- **Clinic and OR (CO)**: Combined set {1,2,5} [Assign_ClinicOR]

### Decision Variables

- **x(j,t,k)**: Binary assignment variable (1 if physician j is assigned to task k on day t)
- **clinic(j,t,s)**: Integer; number of clinic appointments performed
- **surgery(j,t,s)**: Integer; number of surgeries performed
- **clinicwait(t,s)**: Nonnegative; clinic queue length
- **surgerywait(j,t,s)**: Nonnegative; surgery queue length

### Key Parameters

- **Demand(t,s)**: Patient arrivals into clinic
- **ClinicCapacity(j,t)**: Maximum clinic appointments per physician per day
- **SurgeryCapacity(j,t,k)**: OR capacity for OR types (k∈{1,5})
- **PhysicianAssign(j,t,k)**: Binary mask (1 if assignment is allowed)
- **MaxOR(t)**: Maximum OR usage per day (weighted)
- **Alpha**: Proportion of clinic patients requiring surgery
- **Beta**: Generic adjustment parameter

---

## Nomenclature Mapping

This section provides a comprehensive mapping between the nomenclature used in the academic paper, the AIMMS model implementation, and the Streamlit user interface. This mapping helps ensure consistency and clarity when referencing concepts across different contexts.

### Indices and Sets

| Paper Notation | AIMMS Model | Streamlit Interface | Description |
|----------------|-------------|---------------------|-------------|
| p | j | j | Physician index (1..NumberOfPhysicians) |
| P | Physicians | Physicians | Set of all physicians |
| T | Days | Days | Set of planning days (1..PlanHorizon) |
| t | t | t | Day/time period index |
| Ξ | Scenarios | Scenarios | Set of uncertainty scenarios |
| s | s | s | Scenario index (1..TotalScenarios) |
| K | Assign | Assign | Set of assignment types |
| k | k | k | Assignment type index |
| O | Assign_OR | OR types | OR assignment types {1,5} |
| C | Assign_Clinic | Clinic type | Clinic assignment type {2} |
| CO | Assign_ClinicOR | Clinic/OR types | Combined set {1,2,5} |
| τ | DaysOtherTasks | DaysOtherTasks | Subset of days for other tasks |

### Decision Variables

| Paper Notation | AIMMS Model | Streamlit Interface | Description |
|----------------|-------------|---------------------|-------------|
| x_{p,t,k} | x(j,t,k) | x | Binary assignment variable (1 if physician j assigned to task k on day t) |
| c_{p,t,ξ} | clinic(j,t,s) | clinic | Number of clinic appointments performed |
| s_{p,t,ξ} | surgery(j,t,s) | surgery | Number of surgeries performed |
| w^c_{t,ξ} | clinicwait(t,s) | clinicwait | Clinic queue length (nonnegative) |
| w^s_{p,t,ξ} | surgerywait(j,t,s) | surgerywait | Surgery queue length per physician (nonnegative) |
| d^s_{p,t,ξ} | surgerydemand(j,t,s) | surgerydemand | Surgery demand per physician |

### Parameters

| Paper Notation | AIMMS Model | Streamlit Interface | Description |
|----------------|-------------|---------------------|-------------|
| T | PlanHorizon | Planning horizon (days) | Total number of planning days |
| |P| | NumberOfPhysicians | Number of physicians | Total number of physicians |
| |Ξ| | TotalScenarios | Number of scenarios | Total number of uncertainty scenarios |
| α | Alpha | Alpha | Proportion of clinic patients requiring surgery |
| β | Beta | Beta | Generic adjustment parameter (prioritization factor) |
| λ_{t,ξ} | Demand(t,s) | Demand | Patient arrivals into clinic |
| C^c_{p,t} | ClinicCapacity(j,t) | ClinicCapacity | Maximum clinic appointments per physician per day |
| C^s_{p,t,k} | SurgeryCapacity(j,t,k) | SurgeryCapacity | OR capacity for OR types (k∈{1,5}) |
| A_{p,t,k} | PhysicianAssign(j,t,k) | PhysicianAssign2 | Binary mask (1 if assignment is allowed) |
| M_t | MaxOR(t) | MaxOR | Maximum OR usage per day (weighted) |
| M^min | MinOR | Minimum ORs per day | Lower bound on daily OR usage in late period |
| | MaxConsecutiveORDays | Max consecutive OR days | Upper bound of consecutive OR days per physician |
| | BeginChangeAdmPTO | Admin/PTO window start day | Start day for Admin/PTO minimum checks |
| | EndChangeAdmPTO | Admin/PTO window end day | End day for Admin/PTO minimum checks |
| | TwoORWindowStart | 2OR window start | First day allowing 2OR usage |
| | TwoORWindowEnd | 2OR window end | Last day allowing 2OR usage |
| Q^c_0 | InicialQueueClinic | Initial clinic queue | Scalar initial clinic queue |
| Q^s_{p,0} | InicialQueueSurgery(j) | InicialQueueSurgery | Initial surgery queue per physician |
| | MinARE(j) | MinARE | Minimum ARE assignments per physician |
| | ORMultiplier(k) | ORMultiplier | OR weight per assignment type |
| | MinTypePerDay(j,k) | MinTypePerDay | Minimum assignments per physician and type over Admin/PTO window |

### Objective Function

| Paper Notation | AIMMS Model | Description |
|----------------|-------------|-------------|
| Z | Z | Objective function value |

### Notes on Nomenclature

1. **Index Notation**: The paper uses mathematical notation (subscripts), while the implementation uses function-style notation with parentheses. For example, `x_{p,t,k}` in the paper corresponds to `x(j,t,k)` in AIMMS.

2. **Greek Letters**: Some parameters use Greek letters in the paper (α, β, τ, Ξ) but are written in ASCII in the implementation (Alpha, Beta, DaysOtherTasks, Scenarios).

3. **Case Sensitivity**: AIMMS is case-sensitive, so parameter names must match exactly. The Streamlit interface uses mixed case for readability but maps to exact AIMMS names.

4. **Interface Labels**: The Streamlit interface uses descriptive labels (e.g., "Planning horizon (days)") with the AIMMS parameter name in brackets (e.g., "[PlanHorizon]") to help users understand the mapping.

5. **Not Exposed Parameters**: Some parameters exist in the AIMMS model but are not currently exposed in the Streamlit interface. These are marked as "(Not exposed)" in the mapping table.

---

## Input File Formats

All input files are CSV format with specific column requirements:

### Scalar Parameters

Scalar parameters are saved as single-row CSVs:
- **PlanHorizon.csv**: `PlanHorizon\n111`
- **NumberOfPhysicians.csv**: `NumberOfPhysicians\n10`
- **Alpha.csv**: `Alpha\n0.68`

### Table Parameters

Table parameters require index columns plus a 'value' column:

#### Demand.csv
- **Columns**: `t, s, value`
- **Description**: Patient arrivals by day and scenario
- **Example**:
  ```csv
  t,s,value
  1,1,12
  1,2,15
  2,1,18
  ```

#### ClinicCapacity.csv
- **Columns**: `j, t, value`
- **Description**: Maximum clinic appointments per physician per day
- **Example**:
  ```csv
  j,t,value
  1,1,8
  1,2,8
  2,1,8
  ```

#### SurgeryCapacity.csv
- **Columns**: `j, t, k, value`
- **Description**: OR capacity (only for k=1 and k=5)
- **Example**:
  ```csv
  j,t,k,value
  1,1,1,3
  1,1,5,3
  2,1,1,3
  ```

#### PhysicianAssign2.csv
- **Columns**: `j, t, k, value`
- **Description**: Binary mask (1=allowed, 0=not allowed)
- **Example**:
  ```csv
  j,t,k,value
  1,1,1,1
  1,1,2,1
  1,1,3,1
  ```

#### MaxOR.csv
- **Columns**: `t, value`
- **Description**: Maximum OR usage per day (weighted)
- **Example**:
  ```csv
  t,value
  1,2
  2,2
  ```

### Set Files

#### Assign.csv
- **Columns**: `k`
- **Description**: List of all assignment types
- **Example**:
  ```csv
  k
  1
  2
  3
  4
  5
  6
  7
  ```

#### isOR.csv, isClinic.csv, isCO.csv
- **Columns**: `k, value`
- **Description**: Binary flags indicating category membership
- **Example** (isOR.csv):
  ```csv
  k,value
  1,1
  5,1
  ```

### Column Name Normalization

The application accepts multiple column name variations:
- Time index: `t`, `T`, `day`, `Day`
- Physician index: `j`, `J`, `physician`, `Physician`
- Scenario index: `s`, `S`, `scenario`, `Scenario`
- Assignment type: `k`, `K`, `type`, `Type`
- Value column: `value`, `Value`, `valor`, `VALOR`, `val`

---

## Output File Formats

### AIMMS Output Text File

The primary output is `aimms_output.txt` containing:
- Objective function value: `Z := 1234.56 ;`
- Variable assignments: `x(j,t,k) := data { (1,1,1) : 1, (1,1,2) : 0, ... } ;`

### Parsed CSV Files

The application generates CSV files for each variable:

#### x.csv (Assignments)
- **Columns**: `j, t, k, value`
- **Description**: Binary assignment decisions

#### clinic.csv
- **Columns**: `j, t, s, value`
- **Description**: Clinic appointments performed

#### surgery.csv
- **Columns**: `j, t, s, value`
- **Description**: Surgeries performed

#### clinicwait.csv
- **Columns**: `t, s, value`
- **Description**: Clinic queue length over time

#### surgerywait.csv
- **Columns**: `j, t, s, value`
- **Description**: Surgery queue length per physician

---

## Function Reference

### Core Functions

#### `ensure_dirs()`
Creates runtime directories if they don't exist. Called at application startup.

#### `load_config() -> dict`
Loads configuration from `config.yaml` or returns defaults. Handles automatic project file detection.

#### `save_uploaded_csv(file, target_name: str) -> Optional[Path]`
Saves uploaded CSV files to runtime_input directory with standardized names.

#### `save_scalar_to_csv(name: str, value) -> Path`
Converts scalar parameters to single-row CSV format.

### AIMMS Text Generation

#### `append_aimms_text_for_scalar(buf, name, value)`
Appends AIMMS syntax for scalar assignments to a text buffer.

#### `append_aimms_text_for_table(buf, name, path, index_cols)`
Converts CSV table data to AIMMS text format with proper syntax.

#### `append_aimms_text_for_set(buf, name, path, col)`
Converts CSV set data to AIMMS set syntax.

#### `build_and_write_aimms_input_text(scalars, csv_paths) -> Path`
Consolidates all inputs into a single AIMMS text file.

### Data Generation

#### `write_generated_csv(name, dest_path, plan_horizon, total_scenarios, num_physicians) -> bool`
Generates default CSV files for testing when user data is not available.

#### `ensure_template_if_missing(name, dest_path, ...) -> bool`
Ensures a CSV exists by copying from templates or generating defaults.

#### `populate_templates_dir(plan_horizon, total_scenarios, num_physicians) -> list[str]`
Populates the templates directory with default files.

### Validation

#### `compute_max_t_from_csvs(csv_paths) -> Optional[int]`
Finds the maximum time period across all CSV files for validation.

#### `compute_max_s_from_demand(demand_path) -> Optional[int]`
Finds the maximum scenario index in demand data for validation.

### AIMMS Execution

#### `run_aimms(aimms_exe_path, project_path, procedure, use_powershell) -> CompletedProcess`
Executes AIMMS in batch mode. Handles Windows/WSL path conversion and PowerShell integration.

### Results Processing

#### `parse_aimms_output(output_path) -> Dict`
Parses AIMMS output text file and extracts variables and objective value.

#### `variable_to_csv(var_name, entries) -> pd.DataFrame`
Converts parsed variable data to structured DataFrames.

#### `create_csvs_from_output(output_path, output_dir) -> Dict[str, Path]`
Orchestrates parsing and CSV creation for all variables.

---

## Configuration

### config.yaml

Create a `config.yaml` file in the `streamlit_app/` directory:

```yaml
aimms_exe_path: "C:\\Program Files\\AIMMS\\AIMMS 4.92\\bin\\AIMMS.exe"
project_path: "C:\\Users\\YourName\\Documents\\app_paper\\model_aimms\\CPLAN.aimms"
run_procedure: "RunOptimizationFromCSV"
use_powershell: true
```

### Configuration Keys

- **aimms_exe_path**: Absolute path to AIMMS executable (Windows .exe)
- **project_path**: Absolute path to the .aimms project file
- **run_procedure**: AIMMS procedure name to execute (default: "RunOptimizationFromCSV")
- **use_powershell**: Whether to use PowerShell for execution (recommended on WSL)

### Automatic Detection

The application automatically detects the project file location:
1. First tries: `model_aimms/CPLAN.aimms`
2. Falls back to: `Stochastic SAA CERTO StoHard Cap8/CPLAN.aimms`

---

## Troubleshooting

### Common Issues

#### AIMMS Executable Not Found
**Problem**: Error "AIMMS not found: [path]"

**Solutions**:
- Verify the path in `config.yaml` is correct
- Use absolute paths (not relative)
- On WSL, ensure the path is accessible from Windows
- Check file permissions

#### Project File Not Found
**Problem**: Error ".aimms project not found: [path]"

**Solutions**:
- Verify the project path in `config.yaml`
- Ensure the .aimms file exists at the specified location
- Check that the file extension is correct (.aimms)

#### CSV Validation Errors
**Problem**: Warnings about missing columns or invalid data

**Solutions**:
- Check that CSV files have required columns (t, j, s, k, value)
- Verify column names match expected format (case-insensitive)
- Ensure numeric columns contain valid numbers
- Check for missing values in required columns

#### Execution Fails
**Problem**: AIMMS execution returns non-zero exit code

**Solutions**:
- Check AIMMS stdout/stderr in the "Process output" section
- Verify all required input files are present
- Check AIMMS license and version compatibility
- Review AIMMS log files in the model directory

#### Results Not Displayed
**Problem**: No results appear after execution

**Solutions**:
- Verify `aimms_output.txt` exists in `runtime_output/`
- Check that the AIMMS procedure exports results correctly
- Review the output file format matches expected syntax
- Check file permissions for reading output files

### Debugging Tips

1. **Check Runtime Directories**: Verify files are being saved to `runtime_input/`
2. **Review AIMMS Output**: Expand "View detailed output" to see execution logs
3. **Validate Input Files**: Use the data dictionary expander to verify file formats
4. **Test with Templates**: Start with template files to ensure basic functionality
5. **Check Session State**: Refresh the page if session state seems corrupted

---

## Maintenance Guide

### Adding New Parameters

1. **Add UI Input**: Add a new input field in the "Model parameters" section
2. **Save Scalar**: Add `save_scalar_to_csv()` call in the save button handler
3. **Update Mapping**: Add entry to `mapping` dict in `build_and_write_aimms_input_text()`
4. **Update Templates**: Add default generation logic in `write_generated_csv()`

### Adding New Input Files

1. **Add File Uploader**: Add `st.file_uploader()` in the "Input data" section
2. **Handle Upload**: Add save logic in the save button handler
3. **Add to Mapping**: Include in the CSV-to-AIMMS mapping
4. **Document Format**: Update this documentation with file format requirements

### Extending Results Visualization

1. **Parse New Variable**: Add parsing logic in `parse_aimms_output()`
2. **Create CSV**: Add conversion logic in `variable_to_csv()`
3. **Add Tab/Display**: Add visualization section in the Results area
4. **Update Documentation**: Document the new output format

### Modifying AIMMS Integration

1. **Update Procedure**: Change `run_procedure` default or config
2. **Adjust Flags**: Modify command-line flags in `run_aimms()` if needed
3. **Handle Versions**: Add version-specific logic if supporting multiple AIMMS versions
4. **Test Execution**: Verify batch mode execution works correctly

### Code Organization

- **Functions**: Group related functions together (data handling, AIMMS integration, etc.)
- **Constants**: Keep path constants at the top of the file
- **Section Comments**: Use clear section dividers in `main()` function
- **Error Handling**: Provide user-friendly error messages via Streamlit

### Testing

1. **Unit Tests**: Test individual functions with sample data
2. **Integration Tests**: Test full workflow from input to output
3. **Edge Cases**: Test with missing files, invalid data, empty results
4. **User Testing**: Get feedback from end users on interface usability

### Performance Considerations

- **Large Files**: Consider chunking for very large CSV files
- **Caching**: Use Streamlit caching for expensive operations
- **Parallel Processing**: Consider parallel CSV processing if needed
- **Memory**: Monitor memory usage with large datasets

---

## Additional Resources

- **AIMMS Documentation**: Consult AIMMS user guide for procedure syntax
- **Streamlit Documentation**: https://docs.streamlit.io/
- **Pandas Documentation**: https://pandas.pydata.org/docs/
- **Model Paper**: Refer to the academic paper for model formulation details

---

## Version History

- **v1.0**: Initial release with core functionality
  - Parameter configuration
  - CSV file upload and validation
  - AIMMS batch execution
  - Results parsing and visualization

---

## Contact and Support

For issues, questions, or contributions, please refer to the project repository or contact the development team.
