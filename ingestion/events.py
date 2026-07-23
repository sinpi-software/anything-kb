EVENTS_NAMESPACE = "software.sinpi.desk"

PREFECT_EVENTS_NAMESPACE = f"{EVENTS_NAMESPACE}.rss_feed"
RSS_FEED_FETCHED_EVENT_NAME = f"{PREFECT_EVENTS_NAMESPACE}.fetched"

# Emitted when a feed item has been extracted to markdown; triggers the transform pipeline.
MARKDOWN_ARTIFACT_CREATED_EVENT = f"{EVENTS_NAMESPACE}.markdown_artifact.created"
