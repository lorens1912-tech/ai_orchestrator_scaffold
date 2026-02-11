from fastapi import APIRouter

router = APIRouter(prefix="/project-truth", tags=["project-truth"])

@router.get("/health")
def project_truth_health():
    return {"ok": True, "source": "stub"}
