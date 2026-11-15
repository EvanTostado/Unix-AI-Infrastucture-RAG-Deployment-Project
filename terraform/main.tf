

terraform {
  required_providers {

    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = "linux-ai-project"
  region  = "us-east1"
}

resource "google_compute_network" "main_vpc" {
  name                    = "linux-ai-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main_subnet" {
  name          = "linux-ai-subnet"
  ip_cidr_range = "10.10.0.0/24"
  region        = "us-east1"
  network       = google_compute_network.main_vpc.id


  private_ip_google_access = true
}



resource "google_compute_firewall" "allow_app_to_inference" {
  name      = "allow-app-to-inference"
  network   = google_compute_network.main_vpc.name
  direction = "INGRESS"
  priority  = 1000


  source_tags = ["app-server"]
  target_tags = ["inference-server"]


  allow {
    protocol = "tcp"
    ports    = ["11434"]
  }
}



resource "google_compute_firewall" "allow_app_health_checks" {
  name      = "allow-app-health-checks"
  network   = google_compute_network.main_vpc.name
  direction = "INGRESS"

  source_ranges = [
    "35.191.0.0/16",
    "130.211.0.0/22"

  ]

  target_tags = ["app-server"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}


resource "google_compute_instance" "test_vm" {
  name         = "linux-ai-test-vm"
  machine_type = "e2-medium"
  zone         = "us-east1-b"


  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 20
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main_subnet.id

    #Temporary external IP for downloading packages
    access_config {}
  }
  metadata_startup_script = file(
    "${path.module}/../scripts/vm-startup.sh"
  )

  tags = ["test-vm"]
}



resource "google_compute_firewall" "allow_iap_ssh" {
  name      = "allow-iap-ssh"
  network   = google_compute_network.main_vpc.name
  direction = "INGRESS"


  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["test-vm"]


  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
