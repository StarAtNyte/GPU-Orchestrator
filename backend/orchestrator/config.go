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
	ID              string         `yaml:"id"`
	Name            string         `yaml:"name"`
	Type            string         `yaml:"type"` // "local" or "modal"
	Queue           string         `yaml:"queue"`
	Endpoint        string         `yaml:"endpoint"`
	Description     string         `yaml:"description"`
	GPUVramGB       int            `yaml:"gpu_vram_gb"`
	DockerImage     string         `yaml:"docker_image"`
	TimeoutSeconds  int            `yaml:"timeout_seconds"`
	Parameters      []AppParameter `yaml:"parameters"`
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

	// Convert to map for easy lookups
	appMap := make(map[string]AppConfig)
	for _, app := range registry.Apps {
		appMap[app.ID] = app
		log.Printf("[INFO] Loaded app: %s (%s) - Type: %s", app.ID, app.Name, app.Type)
	}

	return appMap, nil
}
