import axios, { AxiosError } from 'axios';
import { isTauriDesktop } from '../lib/desktopBridge';
import { createTauriAdapter } from './tauriAdapter';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

try {
  if (isTauriDesktop()) api.defaults.adapter = createTauriAdapter();
} catch (error) {
  // A desktop initialization failure must not silently send requests via HTTP.
  api.defaults.adapter = (config) => Promise.reject(AxiosError.from(error, AxiosError.ERR_NETWORK, config));
}

export default api;
