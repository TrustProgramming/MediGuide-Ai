# Additional Medical Datasets

Place up to three real, licensed medical symptom CSV datasets in this folder before retraining.

Supported columns:

- Disease label: `disease`, `diagnosis`, `prognosis`, or `label`
- Symptoms as text: `symptoms`, `symptom`, or `text`
- Or one-hot symptom columns containing `1`, `true`, or `yes`

Run training from the project root:

```powershell
.venv\Scripts\python.exe -m python_backend.app.agents.kaggle_training
```

The generated model and evaluation report list every training source. Do not use patient-identifiable data or datasets without permission, and treat all benchmark scores as non-clinical.