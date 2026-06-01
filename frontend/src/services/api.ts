import axios from 'axios';
import { useAuthStore } from '@/store/authStore';

export const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

let _refreshing: Promise<string | null> | null = null;

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && !originalRequest._retried) {
      originalRequest._retried = true;

      // Attempt a token refresh before giving up
      if (!_refreshing) {
        _refreshing = api
          .post<{ access_token: string }>('/auth/refresh')
          .then((res) => {
            const newToken = res.data.access_token;
            useAuthStore.getState().setToken(newToken);
            return newToken;
          })
          .catch(() => null)
          .finally(() => { _refreshing = null; });
      }

      const newToken = await _refreshing;
      if (newToken) {
        originalRequest.headers['Authorization'] = `Bearer ${newToken}`;
        return api(originalRequest);
      }

      // Refresh failed — log out
      useAuthStore.getState().logout();
      window.location.href = '/login';
    }

    if (error.response?.status === 503 && error.response?.data?.detail?.includes('Setup required')) {
      window.location.href = '/setup';
    }

    return Promise.reject(error);
  }
);
