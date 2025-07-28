from diagrams import Diagram, Cluster, Edge
from diagrams.gcp.analytics import BigQuery
from diagrams.gcp.storage import GCS
from diagrams.gcp.devtools import Scheduler
from diagrams.gcp.compute import Run
from diagrams.onprem.client import Users
from diagrams.custom import Custom
from diagrams.onprem.container import Docker

with Diagram("Franchise Data Enrichment Pipeline", show=True, direction="LR"):
    scheduler = Scheduler("Cloud Scheduler\n(9:00 AM EST)")

    with Cluster("Trigger Flow"):
        trigger_pubsub = Custom("Pub/Sub Trigger Topic", "images_gcp/PubSub.png")
        
        # Inner cluster just for Cloud Run + Docker
        with Cluster("Cloud Run"):
            cloud_run = Run("Reads Excel\nScrapes Data\nWrites CSV")
            docker_icon = Docker("Docker")

    with Cluster("Storage & Output"):
        gcs = GCS("Google Cloud Storage\n(input Excel + enriched CSV)")
        bq = BigQuery("BigQuery\n(External Table)")

    with Cluster("Visualization & Alerts"):
        looker = Custom("Looker Dashboard", "images_gcp/Looker.png")
        notify_pubsub = Custom("Pub/Sub Notification Topic", "images_gcp/PubSub.png")

    # Data flow
    scheduler >> Edge(minlen="2") >> trigger_pubsub >> Edge(minlen="2") >> cloud_run
    cloud_run >> Edge(minlen="2") >> gcs
    gcs >> Edge(minlen="2") >> cloud_run
    gcs >> Edge(minlen="2") >> bq
    bq >> Edge(minlen="2") >> looker
    cloud_run >> Edge(minlen="2") >> notify_pubsub
    notify_pubsub >> Edge(minlen="2") >> bq