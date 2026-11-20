
#terraform configuration
terraform { 
  required_providers { #declare providers

    google = {
      source  = "hashicorp/google" #location of provider
      version = "~> 7.0" #specify verions 7.0
    }
  }
}
#google cloud provider configuration
provider "google" {
  project = "linux-ai-project" 
  region  = "us-east1" #set default region
}
#create my VPC network
resource "google_compute_network" "main_vpc" { #tell terraform to manage resource and type
  name                    = "linux-ai-vpc" # name of the VPC 
  auto_create_subnetworks = false #disable google automatic subnet creation
}
#creating the subnet
resource "google_compute_subnetwork" "main_subnet" {
  name          = "linux-ai-subnet"
  ip_cidr_range = "10.10.0.0/24" #network address cidr /24 first 24 bits identify network
  region        = "us-east1"
  network       = google_compute_network.main_vpc.id #links subnet to my VPC resourcetype.resourcename.attribute

#allow VMs with prive ip only in this subnet to access(not general internet access)
  private_ip_google_access = true
}


#firewall - allow app servers to communicate with Ollama
resource "google_compute_firewall" "allow_app_to_inference" {
  name      = "allow-app-to-inference"
  network   = google_compute_network.main_vpc.name #associate firewall rule with my VPC
  direction = "INGRESS" #incoming network traffic
  priority  = 1000 # set rule's precedence(small number = high priority)


  source_tags = ["app-server"] #vm with app-server tag can send traffic
  target_tags = ["inference-server"] #vm with inference-server tag receive traffic


  allow {
    protocol = "tcp"
    ports    = ["11434"] #ollama api listens on TCP port 11434
  }
}


#allow google cloud health checks
resource "google_compute_firewall" "allow_app_health_checks" {
  name      = "allow-app-health-checks"
  network   = google_compute_network.main_vpc.name
  direction = "INGRESS"
#health ranges used by load-balancing health check systems
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

#create ubunto vm
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
#attach vm to subnet created already
  network_interface {
    subnetwork = google_compute_subnetwork.main_subnet.id

    #Temporary external IP for downloading packages
    access_config {}
  }
#startup script
  metadata_startup_script = file( #supply the vm startup script
    "${path.module}/../scripts/vm-startup.sh" # path to bash script
  )

  tags = ["test-vm"]
}

#firewall - allow ssh through iap

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
