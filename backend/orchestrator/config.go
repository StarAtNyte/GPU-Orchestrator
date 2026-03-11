package main

import (
	"fmt"
	"io/ioutil"
	"log"

	"gopkg.in/yaml.v3"
)

// AppParameter represents a parameter definition for an app
type AppParameter struct {
	Name        string      `yaml:"name"`
	Type        string      `yaml:"type"`
	Required    bool        `yaml:"required"`
	Default     interface{} `yaml:"default"`
	Description string      `yaml:"description"`
}

// AppConfig represents a single app configuration
type AppConfig struct {
	ID                    string         `yaml:"id"`
	Name                  string         `yaml:"name"`
	Type                  string         `yaml:"type"` // "local" or "modal"
	Queue                 string         `yaml:"queue"`
	Endpoint              string         `yaml:"endpoint"`
	Description           string         `yaml:"description"`
	GPUVramGB             int            `yaml:"gpu_vram_gb"`
	DockerImage           string         `yaml:"docker_image"`
	ContainerName         string         `yaml:"container_name"`
	IdleTimeoutSeconds    int            `yaml:"idle_timeout_seconds"`
	StartupTimeoutSeconds int            `yaml:"startup_timeout_seconds"`
	TimeoutSeconds        int            `yaml:"timeout_seconds"`
	CleanupPort           int               `yaml:"cleanup_port"` // HTTP port for /cleanup endpoint (default: 8000)
	Environment           map[string]string `yaml:"environment"`  // Extra env vars passed to the container (e.g. N_CTX, MODEL_DIR)
	Volumes               []string          `yaml:"volumes"`      // Extra bind-mount strings ("host:container[:opts]")
	SaveHistory           *bool             `yaml:"save_history"`
	Parameters            []AppParameter    `yaml:"parameters"`
}

// AppRegistry holds all app configurations
type AppRegistry struct {
	Apps []AppConfig `yaml:"apps"`
}

// LoadAppRegistry loads the app registry from a YAML file
func LoadAppRegistry(filePath string) (map[string]AppConfig, error) {
	data, err := ioutil.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	var registry AppRegistry
	if err := yaml.Unmarshal(data, &registry); err != nil {
		return nil, fmt.Errorf("failed to parse YAML: %w", err)
	}

	// Validate and convert to map for easy lookups
	appMap := make(map[string]AppConfig)
	for _, app := range registry.Apps {
		if app.ID == "" {
			return nil, fmt.Errorf("app entry has empty id")
		}
		if app.Type != "local" && app.Type != "modal" {
			return nil, fmt.Errorf("app %q has invalid type %q (must be 'local' or 'modal')", app.ID, app.Type)
		}
		if app.StartupTimeoutSeconds < 0 {
			return nil, fmt.Errorf("app %q has negative startup_timeout_seconds", app.ID)
		}
		if app.IdleTimeoutSeconds < 0 {
			return nil, fmt.Errorf("app %q has negative idle_timeout_seconds", app.ID)
		}
		appMap[app.ID] = app
		log.Printf("[INFO] Loaded app: %s (%s) - Type: %s", app.ID, app.Name, app.Type)
	}

	return appMap, nil
}
