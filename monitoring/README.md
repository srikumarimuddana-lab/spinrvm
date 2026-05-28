Mount `spinr_slo_alerts.yaml` at `/etc/grafana/provisioning/alerting/` in the Grafana container.
Restart Grafana to load the rules. Requires a `prometheus` datasource UID matching the YAML.
