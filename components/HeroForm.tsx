import React, { useState } from 'react';
import { RegistrationQuery } from '../types';
import { Input, Button } from './ui';

interface HeroFormProps {
  onSubmit: (data: RegistrationQuery) => void;
  isLoading: boolean;
}

// UK registration plate patterns - supports multiple formats:
// - Current format (2001+): AA00 AAA (e.g., AB12 CDE)
// - Prefix format (1983-2001): A000 AAA (e.g., P123 ABC)
// - Suffix format (1963-1983): AAA 000A (e.g., ABC 123D)
// - Northern Ireland: AAA 0000 (e.g., ABC 1234)
// - Personalized: Various formats with numbers and letters
const UK_REG_PATTERN = /^[A-Z0-9]{2,7}$/i;

// UK postcode pattern
const UK_POSTCODE_PATTERN = /^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$/i;

const validateRegistration = (value: string): string | undefined => {
  if (!value) return 'Registration is required';
  const cleaned = value.replace(/\s/g, '');
  if (cleaned.length < 2) return 'Registration is too short';
  if (cleaned.length > 7) return 'Registration is too long';
  if (!UK_REG_PATTERN.test(cleaned)) {
    return 'Enter a valid UK registration';
  }
  return undefined;
};

const validatePostcode = (value: string): string | undefined => {
  if (!value) return 'Postcode is required';
  if (value.length < 3) return 'Postcode is too short';
  if (!UK_POSTCODE_PATTERN.test(value.replace(/\s/g, ''))) {
    return 'Enter a valid UK postcode (e.g. SW1A 1AA)';
  }
  return undefined;
};

const HeroForm: React.FC<HeroFormProps> = ({ onSubmit, isLoading }) => {
  const [registration, setRegistration] = useState('');
  const [postcode, setPostcode] = useState('');
  const [touched, setTouched] = useState({ registration: false, postcode: false });

  const registrationError = touched.registration ? validateRegistration(registration) : undefined;
  const postcodeError = touched.postcode ? validatePostcode(postcode) : undefined;

  const isFormValid =
    !validateRegistration(registration) &&
    !validatePostcode(postcode) &&
    !isLoading;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched({ registration: true, postcode: true });

    if (isFormValid) {
      onSubmit({ registration, postcode });
    }
  };

  return (
    <div className="w-full max-w-[500px] bg-white rounded-2xl shadow-sm p-8 md:p-10">
      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        <Input
          id="registration"
          label="Registration Number"
          placeholder="e.g. AB12 CDE"
          value={registration}
          onChange={setRegistration}
          onBlur={() => setTouched(t => ({ ...t, registration: true }))}
          error={registrationError}
          success={touched.registration && !registrationError}
          maxLength={8}
          uppercase
          required
          aria-label="Enter registration for MOT history check"
        />

        <Input
          id="postcode"
          label="Post Code"
          placeholder="e.g. SW1A 1AA"
          value={postcode}
          onChange={setPostcode}
          onBlur={() => setTouched(t => ({ ...t, postcode: true }))}
          error={postcodeError}
          success={touched.postcode && !postcodeError}
          uppercase
          required
        />

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          loading={isLoading}
          loadingText="Checking vehicle..."
          disabled={!isFormValid}
          className="mt-4"
        >
          Check This Car
        </Button>
      </form>
    </div>
  );
};

export default HeroForm;
