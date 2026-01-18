import React, { useState } from 'react';
import { RegistrationQuery } from '../types';
import { Loader2 } from './Icons';

interface HeroFormProps {
  onSubmit: (data: RegistrationQuery) => void;
  isLoading: boolean;
}

const HeroForm: React.FC<HeroFormProps> = ({ onSubmit, isLoading }) => {
  const [registration, setRegistration] = useState('');
  const [postcode, setPostcode] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (registration && postcode) {
      onSubmit({
        registration,
        postcode
      });
    }
  };

  const isFormValid = registration.length >= 2 && postcode.length >= 3 && !isLoading;

  const inputClassName = "w-full px-4 py-3.5 bg-white border border-slate-200 rounded-lg appearance-none focus:outline-none focus:ring-1 focus:ring-slate-900 focus:border-slate-900 transition-all text-slate-900 placeholder-slate-400";
  const labelClassName = "block text-sm text-slate-500 mb-1.5 ml-1";

  return (
    <div className="w-full max-w-[500px] bg-white rounded-2xl shadow-sm p-8 md:p-10">
      <form onSubmit={handleSubmit} className="space-y-5">

        {/* Registration Input */}
        <div>
          <label className={labelClassName}>
            Registration Number
          </label>
          <div className="relative">
            <input
              type="text"
              placeholder="e.g. AB12 CDE"
              value={registration}
              onChange={(e) => setRegistration(e.target.value.toUpperCase())}
              className={`${inputClassName} uppercase font-medium tracking-wide`}
              maxLength={8}
            />
            {/* Optional UK plate badge styling cue */}
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                <div className="bg-yellow-400 w-4 h-4 rounded-sm opacity-20"></div>
            </div>
          </div>
        </div>

        {/* Post Code Input */}
        <div>
          <label className={labelClassName}>
            Post Code
          </label>
          <input
            type="text"
            placeholder="e.g. SW1A 1AA"
            value={postcode}
            onChange={(e) => setPostcode(e.target.value.toUpperCase())}
            className={inputClassName}
          />
        </div>

        <button
          type="submit"
          disabled={!isFormValid}
          className="w-full py-4 mt-4 bg-slate-900 text-white rounded-full font-semibold tracking-wide shadow-lg shadow-slate-900/10 hover:bg-black hover:shadow-xl hover:scale-[1.01] active:scale-[0.99] transition-all disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2 uppercase text-sm"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Searching...
            </>
          ) : (
            "Check This Car"
          )}
        </button>
      </form>
    </div>
  );
};

export default HeroForm;
