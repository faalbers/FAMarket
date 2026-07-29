"""
UI-agnostic view logic shared by the API (and, during the migration, still
available to the Streamlit pages).

Nothing in here imports FastAPI or Streamlit: modules take DataFrames / plain
values and return plain values, so the same computation serves any front end
and stays runnable from a script.
"""
