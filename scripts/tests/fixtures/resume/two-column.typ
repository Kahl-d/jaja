// Test fixture: a FICTIONAL two-column resume with a skills table, the layout ATS parsers
// misread. Compiled to PDF inside the tests (typst Python package, bundled with RenderCV);
// ats_check.py must fail it for columns/table layout and extractor disagreement.
#set page(paper: "us-letter", margin: 0.6in)
#set text(font: "Source Sans 3", size: 10.5pt)
#align(center)[
  #text(size: 20pt, weight: "bold")[Riley Moreno] \
  San Francisco, CA | riley.moreno\@example.com | +14155550142
]
#grid(
  columns: (1fr, 2fr),
  column-gutter: 0.4in,
  [
    == Skills
    Python, SQL, TypeScript, PyTorch, scikit-learn, XGBoost, Kubernetes, Docker, Airflow

    == Education
    Harborview State University \
    M.S. in Computer Science \
    Sep 2018 – May 2020

    == Interests
    Trail running, community tutoring, and restoring old radios on weekends
  ],
  [
    == Experience
    *Machine Learning Engineer*, Northwind Robotics | Mar 2023 – Present
    - Built a retrieval-augmented support assistant over 1.2M maintenance logs with PyTorch embeddings and a pgvector index, cutting lookup time from 9 to 4 minutes
    - Designed an evaluation harness of 600 golden questions with an LLM judge that gates every model release in CI
    - Moved batch inference from notebooks to a FastAPI service on Kubernetes serving 40 requests per second

    *Data Scientist*, Contoso Health | Jun 2020 – Feb 2023
    - Trained gradient-boosted readmission models on 3M claims records with scikit-learn and XGBoost
    - Wrote Airflow pipelines with data-quality checks that stopped malformed HL7 feeds
  ],
)
== Tools
#table(
  columns: 3,
  [Tool], [Level], [Years],
  [PyTorch], [Expert], [5],
  [Kubernetes], [Advanced], [3],
  [Airflow], [Advanced], [4],
)
