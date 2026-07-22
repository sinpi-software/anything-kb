from prefect import task
from pydantic import BaseModel

from db import get_postgres_session
from models import Artifact, Transformation


class LLMScoreTransformContext(BaseModel):
    prompt: str


class LLMScoreTransformOutput(BaseModel):
    score: int
    rationale: str


class LLMTransformMetadata(BaseModel): ...


@task
def llm_score_transform(artifact_id: str, scoring_prompt: str) -> tuple[float, str]:
    assert artifact_id is not None
    assert scoring_prompt is not None
    with get_postgres_session() as session:
        artifact = session.get(Artifact, artifact_id)
        assert artifact is not None

        artifact_data = artifact.data

        transformation = Transformation(
            artifact_id=artifact_id,
            context={"prompt": scoring_prompt},
            result={
                "score": ...,
                "rationale": ...,
            },
            metadata=...,
        )

    # take the artifact_data and apply the prompt
    # get the llm response as structured output
    # return it as a tuple

    return (0, "test")


# transform(
#   artifact_id,
#   # want: score
#   # want: rationale
# )

if __name__ == "__main__":
    llm_score_transform()
