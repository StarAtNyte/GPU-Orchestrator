package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
)

type contextKey string

const claimsContextKey contextKey = "claims"

var jwtSecret []byte

// Claims is the JWT payload.
type Claims struct {
	Username string `json:"username"`
	UserID   string `json:"user_id"`
	Role     string `json:"role"`
	jwt.RegisteredClaims
}

// initJWTSecret loads the JWT secret from JWT_SECRET env var or generates a random one.
func initJWTSecret() {
	secret := os.Getenv("JWT_SECRET")
	if secret == "" {
		log.Println("[WARNING] JWT_SECRET not set — generating random secret (tokens won't survive restarts)")
		b := make([]byte, 32)
		if _, err := rand.Read(b); err != nil {
			log.Fatalf("[ERROR] Failed to generate JWT secret: %v", err)
		}
		jwtSecret = b
	} else {
		jwtSecret = []byte(secret)
		log.Println("[INFO] JWT secret loaded from environment")
	}
}

func generateJWT(userID, username, role string) (string, error) {
	claims := Claims{
		Username: username,
		UserID:   userID,
		Role:     role,
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(7 * 24 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(jwtSecret)
}

func parseJWT(tokenString string) (*Claims, error) {
	claims := &Claims{}
	token, err := jwt.ParseWithClaims(tokenString, claims, func(token *jwt.Token) (interface{}, error) {
		if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}
		return jwtSecret, nil
	})
	if err != nil {
		return nil, err
	}
	if !token.Valid {
		return nil, fmt.Errorf("invalid token")
	}
	return claims, nil
}

// jwtAuthMiddleware validates the Bearer token and injects claims into request context.
func jwtAuthMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		setCORSHeaders(w)
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		authHeader := r.Header.Get("Authorization")
		if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized"})
			return
		}

		claims, err := parseJWT(strings.TrimPrefix(authHeader, "Bearer "))
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			json.NewEncoder(w).Encode(map[string]string{"error": "unauthorized"})
			return
		}

		ctx := context.WithValue(r.Context(), claimsContextKey, claims)
		next(w, r.WithContext(ctx))
	}
}

// optionalJwtAuthMiddleware injects claims if a valid token is present, but allows
// unauthenticated requests through (for guest-accessible endpoints).
func optionalJwtAuthMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		setCORSHeaders(w)
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		authHeader := r.Header.Get("Authorization")
		if authHeader != "" && strings.HasPrefix(authHeader, "Bearer ") {
			claims, err := parseJWT(strings.TrimPrefix(authHeader, "Bearer "))
			if err == nil {
				ctx := context.WithValue(r.Context(), claimsContextKey, claims)
				r = r.WithContext(ctx)
			}
		}

		next(w, r)
	}
}

// getUsernameFromContext extracts the authenticated username from request context.
func getUsernameFromContext(r *http.Request) string {
	claims, ok := r.Context().Value(claimsContextKey).(*Claims)
	if !ok || claims == nil {
		return ""
	}
	return claims.Username
}

func setCORSHeaders(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
}

// --- Auth request/response types ---

type SignupRequest struct {
	Username string `json:"username"`
	Email    string `json:"email"`
	Password string `json:"password"`
}

type LoginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type AuthResponse struct {
	Token    string `json:"token"`
	Username string `json:"username"`
}

// --- Handlers ---

func signupHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req SignupRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid JSON"})
		return
	}

	// Validate username
	if len(req.Username) < 3 || len(req.Username) > 30 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "username must be 3–30 characters"})
		return
	}
	if !isValidUsername(req.Username) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "username can only contain letters, numbers, and underscores"})
		return
	}

	// Validate email
	if !strings.Contains(req.Email, "@") || len(req.Email) < 5 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid email"})
		return
	}

	// Validate password
	if len(req.Password) < 8 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "password must be at least 8 characters"})
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		log.Printf("[ERROR] bcrypt error: %v", err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	userID := uuid.New().String()
	username := strings.ToLower(strings.TrimSpace(req.Username))
	email := strings.ToLower(strings.TrimSpace(req.Email))

	_, err = db.Exec(`
		INSERT INTO users (id, username, email, password_hash, created_at)
		VALUES ($1, $2, $3, $4, NOW())
	`, userID, username, email, string(hash))
	if err != nil {
		errStr := err.Error()
		if strings.Contains(errStr, "unique") || strings.Contains(errStr, "duplicate") {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			json.NewEncoder(w).Encode(map[string]string{"error": "username or email already exists"})
			return
		}
		log.Printf("[ERROR] Failed to insert user: %v", err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	token, err := generateJWT(userID, username, "user")
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	log.Printf("[AUTH] New user registered: %s (%s)", username, email)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(AuthResponse{Token: token, Username: username})
}

func loginHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid JSON"})
		return
	}

	var userID, username, hash, role string
	err := db.QueryRow(`
		SELECT id, username, password_hash, role FROM users
		WHERE email = $1 AND is_active = true
	`, strings.ToLower(strings.TrimSpace(req.Email))).Scan(&userID, &username, &hash, &role)

	if err == sql.ErrNoRows {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid credentials"})
		return
	} else if err != nil {
		log.Printf("[ERROR] Login DB error: %v", err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	if err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(req.Password)); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid credentials"})
		return
	}

	db.Exec("UPDATE users SET last_login_at = NOW() WHERE id = $1", userID)

	token, err := generateJWT(userID, username, role)
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	log.Printf("[AUTH] User logged in: %s", username)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(AuthResponse{Token: token, Username: username})
}

func meHandler(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusOK)
		return
	}

	claims := r.Context().Value(claimsContextKey).(*Claims)

	var email string
	var createdAt time.Time
	db.QueryRow("SELECT email, created_at FROM users WHERE id = $1", claims.UserID).Scan(&email, &createdAt)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"username":   claims.Username,
		"email":      email,
		"role":       claims.Role,
		"created_at": createdAt.Format(time.RFC3339),
	})
}

// isValidUsername checks that username contains only letters, digits, and underscores.
func isValidUsername(username string) bool {
	for _, c := range username {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_') {
			return false
		}
	}
	return true
}
