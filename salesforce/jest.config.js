const { jestConfig } = require('@lwc/jest-preset');

module.exports = {
    ...jestConfig,
    testEnvironment: 'jsdom',
    moduleNameMapper: {
        '^@salesforce/apex$': '<rootDir>/jest-mocks/apex.js',
        '^@salesforce/apex/(.+)$': '<rootDir>/jest-mocks/apex.js',
        '^@salesforce/schema/(.+)$': '<rootDir>/jest-mocks/schema.js',
        '^@salesforce/user/(.+)$': '<rootDir>/jest-mocks/user.js',
        '^lightning/navigation$': '<rootDir>/jest-mocks/lightning/navigation.js',
        '^lightning/platformShowToastEvent$':
            '<rootDir>/jest-mocks/lightning/platformShowToastEvent.js',
    },
    coverageThreshold: {
        global: { branches: 80, functions: 80, lines: 80, statements: 80 },
    },
    collectCoverageFrom: [
        'force-app/main/default/lwc/**/*.js',
        '!force-app/main/default/lwc/**/__tests__/**',
    ],
};
