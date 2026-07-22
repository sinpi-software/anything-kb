import os

import dotenv

# .env files live in the project root, one level up from this script's dir
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{project_root}/.env.sample")

os.environ["PREFECT_API_URL"] = str(os.getenv("INGESTION_PREFECT_API_URL"))


def main():
    from rss_feeds import rss_feed_flow

    rss_feed_flow.serve(name="poll-rss-feeds", interval=60 * 60)


if __name__ == "__main__":
    main()
