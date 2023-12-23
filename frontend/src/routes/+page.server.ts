import type { PageServerLoad } from './$types';
import type { UserResponse } from '$lib/types';
import { error } from '@sveltejs/kit';

export const load: PageServerLoad = async ({ fetch }) => {
	async function getUserData() {
		try {
			const res = await fetch('http://127.0.0.1:8080/user');
			if (res.ok) {
				return (await res.json()) as UserResponse;
			}
			error(400, `Unable to fetch user data: ${res.status} ${res.statusText}`);
		} catch (e) {
			console.error(e);
			error(503, 'Unable to fetch user data. API is down.');
		}
	}

	async function getServices() {
		try {
			const res = await fetch('http://127.0.0.1:8080/services');
			if (res.ok) {
				return await res.json();
			}
			error(400, `Unable to fetch services data: ${res.status} ${res.statusText}`);
		} catch (e) {
			console.error(e);
			error(503, 'Unable to fetch services data. API is down.');
		}
	}

	return {
		user: await getUserData(),
		services: getServices()
	};
};
